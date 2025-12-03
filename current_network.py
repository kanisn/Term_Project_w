#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import time
import os
import requests
import csv
from datetime import datetime
from collections import deque

# 설정
RYU_STATS_URL = "http://127.0.0.1:8080/stats"
DECISION_ENGINE_URL = "http://127.0.0.1:5000/metrics"
LOG_JSON_FILE = "latest_metrics.json"
LOG_CSV_FILE = "network_traffic.csv"

# Moving Average를 위한 큐 (최근 3개)
history_video_loss = deque(maxlen=3)
history_video_bps = deque(maxlen=3)
history_dl_bps = deque(maxlen=3)

def init_files():
    """CSV 헤더 생성 및 JSON 초기화"""
    # JSON 초기화
    with open(LOG_JSON_FILE, 'w') as f:
        json.dump([], f)
    
    # CSV 초기화: 모드 'w'로 열어서 항상 새로 작성 (헤더 포함)
    with open(LOG_CSV_FILE, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "vid_rx_mbps", "vid_tx_mbps", "vid_loss_mbps", "vid_loss_percent", "dl_rx_mbps", "dl_tx_mbps", "delay_ms"])
    
    print(f"[INIT] Files initialized (CSV Header Created).")

def estimate_delay(traffic_load_mbps):
    """트래픽 부하에 따른 지연시간 추정 (10Mbps 링크 기준)"""
    LINK_CAPACITY = 10.0
    base_delay = 5.0
    
    if traffic_load_mbps >= LINK_CAPACITY:
        return 500.0 + (traffic_load_mbps - LINK_CAPACITY) * 100
    elif traffic_load_mbps > LINK_CAPACITY * 0.9:
        util = traffic_load_mbps / LINK_CAPACITY
        return base_delay + (100 * util * util)
    else:
        return base_delay + (traffic_load_mbps * 2)

def calculate_moving_average(value, queue):
    """새 값을 큐에 넣고 평균을 반환"""
    queue.append(value)
    return sum(queue) / len(queue)

def main():
    init_files()
    print(f"--- Monitoring & Parsing Started ---")
    
    while True:
        try:
            # 1. Ryu 통계 수집
            res = requests.get(RYU_STATS_URL, timeout=1)
            if res.status_code == 200:
                raw = res.json()
                
                # --- 데이터 가공 (bps -> Mbps) ---
                vid_rx = raw.get('video_bps', 0) / 1e6
                vid_tx = raw.get('video_tx_bps', 0) / 1e6
                dl_rx = raw.get('download_bps', 0) / 1e6
                dl_tx = raw.get('download_tx_bps', 0) / 1e6
                
                vid_loss_mbps = raw.get('video_loss', 0) / 1e6
                
                # Loss % 계산
                loss_percent = 0.0
                if vid_tx > 0:
                    loss_percent = (vid_loss_mbps / vid_tx) * 100

                total_load = vid_rx + dl_rx
                delay = estimate_delay(total_load)

                # --- 2. CSV 저장 (Raw Data) ---
                timestamp = datetime.now().strftime("%H:%M:%S")
                # Append 모드로 데이터 추가
                with open(LOG_CSV_FILE, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        timestamp, 
                        round(vid_rx, 2), round(vid_tx, 2), 
                        round(vid_loss_mbps, 3), round(loss_percent, 2),
                        round(dl_rx, 2), round(dl_tx, 2),
                        round(delay, 1)
                    ])

                # --- 3. JSON 저장 (Moving Average 적용) ---
                # 요구사항 4: 최근 데이터셋 3개를 무빙 에버리지 수행
                avg_vid_loss = calculate_moving_average(loss_percent, history_video_loss)
                
                # JSON에는 마지막(최신 평균) 데이터만 저장
                metrics_data = {
                    "timestamp": timestamp,
                    "video_mbps": round(vid_rx, 2),
                    "download_mbps": round(dl_rx, 2),
                    "video_loss_percent_ma": round(avg_vid_loss, 2), # 이동평균된 Loss
                    "raw_loss_percent": round(loss_percent, 2),      # 현재 Loss
                    "delay_ms": round(delay, 1)
                }

                with open(LOG_JSON_FILE, 'w') as f:
                    json.dump([metrics_data], f, indent=2)

                # --- 4. Decision Engine으로 전송 ---
                # 모니터링 출력
                print(f"[{timestamp}] Load:{total_load:.1f}M | VidLoss(MA):{avg_vid_loss:.1f}% | Push to Engine...")
                
                requests.post(DECISION_ENGINE_URL, json=metrics_data, timeout=1)

            time.sleep(1)

        except Exception as e:
            print(f"[ERROR] {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()