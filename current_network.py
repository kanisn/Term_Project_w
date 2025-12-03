#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import time
import os
import requests
from datetime import datetime

# 설정
RYU_STATS_URL = "http://127.0.0.1:8080/stats"
DECISION_ENGINE_URL = "http://127.0.0.1:5000/metrics"
LOG_FILE = "latest_metrics.json"

# Mininet 병목 링크 용량 (Mbps)
LINK_CAPACITY = 10.0

def init_log_file():
    """파일을 초기화하고 JSON Array 시작을 기록"""
    with open(LOG_FILE, 'w') as f:
        f.write("[\n")
    print(f"[INIT] Log file '{LOG_FILE}' cleared and initialized.")

def log_metrics(metrics):
    """JSON 파일에 메트릭 추가"""
    try:
        # 파일 끝의 ']' 나 이전 ',' 처리를 위해 읽기 모드로 확인하지 않고
        # 단순히 콤마를 찍고 추가함 (Strict JSON을 위해서는 파일을 다 읽어야 하나, 성능상 Append 방식 사용)
        # 단, 첫 줄이 아닐 경우 앞에 콤마 추가
        if os.path.getsize(LOG_FILE) > 5: # '[' 만 있는 경우 제외
            with open(LOG_FILE, 'rb+') as f:
                f.seek(-1, os.SEEK_END)
                # 마지막이 '}' 이면 콤마 추가
                # 여기서는 단순하게 Append 모드로 ', \n {data}' 형태로 씀
        
        with open(LOG_FILE, 'a') as f:
            # 첫 데이터가 아니면 콤마 추가
            if os.path.getsize(LOG_FILE) > 3:
                f.write(",\n")
            json.dump(metrics, f)
    except Exception as e:
        print(f"[LOG ERROR] {e}")

def estimate_delay(traffic_load_mbps):
    """
    트래픽 부하에 따른 예상 지연 시간(ms) 계산 (M/M/1 Queue 모델 유사 적용)
    10Mbps 링크 기준, 9Mbps 넘어가면 지연 급증
    """
    base_delay = 5.0 # 기본 5ms
    
    if traffic_load_mbps >= LINK_CAPACITY:
        return 500.0 + (traffic_load_mbps - LINK_CAPACITY) * 100 # 폭발적 증가
    elif traffic_load_mbps > LINK_CAPACITY * 0.9: # 9Mbps 이상
        # 9Mbps -> 50ms, 9.9Mbps -> 200ms
        util = traffic_load_mbps / LINK_CAPACITY
        return base_delay + (100 * util * util) 
    else:
        return base_delay + (traffic_load_mbps * 2)

def main():
    init_log_file()
    print(f"--- Monitoring Started ---")
    print(f"Polling Ryu ({RYU_STATS_URL}) -> Decision Engine ({DECISION_ENGINE_URL})")
    
    while True:
        try:
            # 1. Ryu에서 통계 수집
            res = requests.get(RYU_STATS_URL, timeout=1)
            if res.status_code == 200:
                raw = res.json()
                
                # 단위 변환 (bps -> Mbps)
                video_mbps = round(raw.get('video_bps', 0) / 1000000, 2)
                dl_mbps = round(raw.get('download_bps', 0) / 1000000, 2)
                total_mbps = round(raw.get('total_bps', 0) / 1000000, 2)
                
                # Loss 계산 (bps 차이 -> 비율 or 유무)
                # 영상 트래픽 손실이 0.1Mbps 이상이면 Loss 발생으로 간주
                video_loss_bps = raw.get('video_loss', 0)
                loss_ratio = 0.0
                if video_mbps > 0:
                    loss_ratio = video_loss_bps / (video_mbps * 1000000 + video_loss_bps)
                
                # Delay 추정
                est_delay = estimate_delay(total_mbps)

                metrics = {
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                    "traffic_load": total_mbps,
                    "video_mbps": video_mbps,
                    "download_mbps": dl_mbps,
                    "delay_ms": round(est_delay, 1),
                    "packet_loss": round(loss_ratio, 4), # 0.0 ~ 1.0
                    "is_congested": total_mbps > 9.0
                }
                
                # 2. 콘솔 출력
                status = "NORMAL"
                if metrics['is_congested']: status = "CONGESTION"
                print(f"[{metrics['timestamp']}] Total: {total_mbps}M (Vid: {video_mbps}M) | Delay: {metrics['delay_ms']}ms | Loss: {metrics['packet_loss']*100:.1f}% | {status}")
                
                # 3. 파일 로깅
                log_metrics(metrics)
                
                # 4. 결정 엔진 전송
                requests.post(DECISION_ENGINE_URL, json=metrics, timeout=1)

            time.sleep(1)

        except KeyboardInterrupt:
            # 파일 닫기 처리
            with open(LOG_FILE, 'a') as f:
                f.write("\n]")
            print("\n[STOP] Log file finalized.")
            break
        except Exception as e:
            print(f"[ERROR] {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()