#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
import time
import os
import sys

# --- 설정 ---
# 주의: 이 스크립트는 TCP를 사용하므로 수신측(h1)에서 
# 'iperf -s -p 5001' (TCP 모드)로 대기하고 있어야 합니다.
TARGET_IP = "10.0.0.1"
TARGET_PORT = 5001 

# 화질별 비트레이트 설정 (해상도, 비트레이트 bps)
QUALITIES = [
    ("360p (Low)",   1_000_000),  # 1 Mbps
    ("480p (SD)",    2_500_000),  # 2.5 Mbps
    ("720p (HD)",    4_000_000),  # 4 Mbps
    ("1080p (FHD)",  6_000_000)   # 6 Mbps (최대)
]

def run_abr_simulation(duration_sec=60):
    print(f"\n[ABR] Connecting to Video Receiver at {TARGET_IP}:{TARGET_PORT} (TCP)...")
    
    try:
        # TCP 소켓 생성 및 연결
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((TARGET_IP, TARGET_PORT))
    except Exception as e:
        print(f"[ERROR] Connection Failed: {e}")
        print("Tip: h1 호스트에서 'iperf -s -p 5001'이 실행 중인지 확인하세요 (UDP 옵션 -u 제외).")
        return

    print("[ABR] Connection Established. Starting Adaptive Streaming...")
    print("------------------------------------------------------------------")
    print(f"{'Time':<8} | {'Quality':<12} | {'Bitrate':<10} | {'TxTime':<8} | {'Status'}")
    print("------------------------------------------------------------------")

    start_time = time.time()
    current_idx = 3  # 시작은 최고 화질(1080p)부터 시도
    
    # 시뮬레이션 루프 (지정된 시간 동안)
    while (time.time() - start_time) < duration_sec:
        quality_name, bitrate = QUALITIES[current_idx]
        
        # 1. 1초 분량의 데이터 크기 계산 (Bytes)
        # TCP는 스트림 방식이므로 패킷 사이즈 상관없이 데이터를 밀어넣음
        chunk_size = int(bitrate / 8)
        payload = b'x' * chunk_size
        
        # 2. 전송 및 시간 측정
        chunk_start = time.time()
        try:
            sock.sendall(payload) # TCP는 버퍼가 찰 때까지 블로킹됨 (네트워크 속도 반영)
        except BrokenPipeError:
            print("[ERROR] Connection closed by remote host.")
            break
        
        chunk_end = time.time()
        tx_time = chunk_end - chunk_start
        
        # 3. ABR 알고리즘 (화질 조정 판단)
        status = "Stable"
        
        # (A) 혼잡 감지: 1초 영상 보내는데 1초 이상 걸림 -> 버퍼링 발생 위기
        if tx_time > 1.0:
            if current_idx > 0:
                current_idx -= 1
                status = "DOWNGRADE (Congestion)"
            else:
                status = "Buffering (Min Quality)"
        
        # (B) 여유 감지: 1초 영상을 0.8초 이내로 아주 빨리 보냄 -> 화질 올려도 됨
        elif tx_time < 0.8:
            if current_idx < len(QUALITIES) - 1:
                current_idx += 1
                status = "UPGRADE (Clear)"
        
        # 로그 출력
        elapsed_total = int(time.time() - start_time)
        print(f"{elapsed_total}s      | {quality_name:<12} | {bitrate/1e6:.1f}M      | {tx_time:.2f}s    | {status}")

        # 4. 페이싱 (Pacing): 너무 빨리 보냈으면 남은 시간만큼 대기 (실시간 재생 흉내)
        # 만약 1초 영상을 0.5초만에 보냈다면, 남은 0.5초는 쉼 (TCP 플로우 컨트롤)
        sleep_time = max(0, 1.0 - tx_time)
        time.sleep(sleep_time)

    print("\n[ABR] Streaming Finished.")
    sock.close()

if __name__ == "__main__":
    try:
        dur = input("재생 시간(초)을 입력하세요 (기본 60): ")
        dur = int(dur) if dur.strip() else 60
        run_abr_simulation(dur)
    except KeyboardInterrupt:
        print("\n[STOP] User interrupted.")