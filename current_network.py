#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import time
import requests
import csv
from datetime import datetime
from collections import deque

# Configuration
RYU_STATS_URL = "http://127.0.0.1:8080/stats"
DECISION_ENGINE_URL = "http://127.0.0.1:5000/metrics"
LOG_JSON_FILE = "latest_metrics.json"
LOG_CSV_FILE = "network_traffic.csv"

# Queues for moving averages (last 3 samples)
history_video_loss = deque(maxlen=3)

# Queues for moving averages (last 10 samples)
history_video_bps = deque(maxlen=10)
history_dl_bps = deque(maxlen=10)


def init_files():
    """Create CSV header and initialize JSON file."""
    # Initialize JSON
    with open(LOG_JSON_FILE, 'w') as f:
        json.dump([], f)

    # Initialize CSV: always start fresh with header
    with open(LOG_CSV_FILE, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["hh:mm:ss", "Total(Mbps)", "Video(Mbps)", "Download(Mbps)", "Video_Loss_3sec_Avg(%)", "Video_loss(%)", "Estimated_Delay(ms)"])

    print(f"[INIT] Files initialized (CSV Header Created).")


def estimate_delay(traffic_load_mbps):
    """Estimate delay based on traffic load (assuming 10 Mbps link)."""
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
    """Append a new value to the queue and return its average."""
    queue.append(value)
    return sum(queue) / len(queue)


def main():
    init_files()
    print(f"--- Monitoring & Parsing Started ---")

    while True:
        try:
            # 1. Collect statistics from Ryu
            res = requests.get(RYU_STATS_URL, timeout=1)
            if res.status_code == 200:
                raw = res.json()

                # --- Data processing (bps -> Mbps) ---
                vid_rx = raw.get('video_bps', 0) / 1e6
                vid_tx = raw.get('video_tx_bps', 0) / 1e6
                dl_rx = raw.get('download_bps', 0) / 1e6
                dl_tx = raw.get('download_tx_bps', 0) / 1e6

                vid_loss_mbps = raw.get('video_loss', 0) / 1e6

                # Calculate loss percentage
                loss_percent = 0.0
                if vid_tx > 0:
                    loss_percent = (vid_loss_mbps / vid_tx) * 100

                total_load = vid_rx + dl_rx
                delay = estimate_delay(total_load)

                # Moving averages for recent video loss and bandwidth (3 and 10 samples)
                avg_vid_loss = calculate_moving_average(loss_percent, history_video_loss)
                avg_vid_bps = calculate_moving_average(vid_rx, history_video_bps)
                avg_dl_bps = calculate_moving_average(dl_rx, history_dl_bps)

                # --- 2. Save CSV (raw data) ---
                timestamp = datetime.now().strftime("%H:%M:%S")
                # Append new row
                with open(LOG_CSV_FILE, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        timestamp,
                        round(total_load, 2),
                        round(vid_rx, 2),
                        round(dl_rx, 2),
                        round(avg_vid_loss, 2),
                        round(loss_percent, 2),
                        round(delay, 1)
                    ])

                # --- 3. Save JSON ---
                # JSON stores only the latest averaged data
                metrics_data = {
                    "timestamp": timestamp,
                    "video_mbps": round(vid_rx, 2),
                    "download_mbps": round(dl_rx, 2),
                    "video_loss_percent_ma": round(avg_vid_loss, 2),  # Moving-average loss
                    "raw_loss_percent": round(loss_percent, 2),      # Instantaneous loss
                    "delay_ms": round(delay, 1),
                    "video_mbps_10sec_avg": round(avg_vid_bps, 1),
                    "download_mbps_10sec_avg": round(avg_dl_bps, 1),
                }

                with open(LOG_JSON_FILE, 'w') as f:
                    json.dump([metrics_data], f, indent=2)

                # --- 4. Send to Decision Engine ---
                # Monitoring output
                print(f"[{timestamp}] Total Load:{total_load:.1f}M | Video(Mbps):{vid_rx:.1f} | Download(Mbps):{dl_rx:.1f}| VidLoss(MA):{avg_vid_loss:.1f}% | Push to Engine...")

                requests.post(DECISION_ENGINE_URL, json=metrics_data, timeout=1)

            time.sleep(1)

        except Exception as e:
            print(f"[ERROR] {e}")
            time.sleep(1)


if __name__ == "__main__":
    main()
