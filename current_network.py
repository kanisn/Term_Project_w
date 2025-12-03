#!/usr/bin/env python3
import random, json, time, os
import requests # 需要导入 requests
import sys # 引入 sys

# --- 新增配置 ---
DECISION_ENGINE_URL = "http://127.0.0.1:5000/metrics" # 新决策服务器的地址
HEADERS = {'Content-Type': 'application/json'}

# --- 定义网络场景 ---
# 场景 (duration_s, load_range, delay_range, loss_range)
# load_range: (min, max) 流量负载 (1.0 视为满载)
# delay_range: (min, max) 延迟 (ms)
# loss_range: (min, max) 丢包率
SCENARIOS = [
    # 场景 1: 正常工作负载 (Low Load)
    ("Low Load (Normal)", 20, (0.4, 0.6), (20, 50), (0.001, 0.005)),
    # 场景 2: 视频流高峰 (High Load - Video Streaming)， 拥塞
    ("Video Surge (High Load)", 30, (0.85, 1.1), (80, 150), (0.005, 0.02)),
    # 场景 3: 视频流 + 大文件下载 (Severe Congestion), 严重拥塞
    ("Congestion (Video + Download)", 40, (1.1, 1.4), (150, 250), (0.02, 0.05)),
    # 场景 4: 负载降低，网络恢复
    ("Recovery (Moderate Load)", 20, (0.6, 0.85), (50, 100), (0.003, 0.01)),
]


def get_metrics(load_range, delay_range, loss_range):
    """根据范围生成模拟指标"""
    return {
        "traffic_load": round(random.uniform(*load_range), 2),
        "delay_ms": round(random.uniform(*delay_range), 1),
        "packet_loss": round(random.uniform(*loss_range), 3)
    }

def send_metrics(metrics):
    """将指标直接发送到决策引擎的 REST API"""
    try:
        r = requests.post(DECISION_ENGINE_URL, json=metrics, headers=HEADERS, timeout=2)
        if r.status_code != 200:
            print(f"[REST CLIENT ERROR] Server responded with status {r.status_code}")
    except requests.exceptions.ConnectionError:
        print("[REST CLIENT ERROR] Could not connect to Decision Engine (Port 5000). Is it running?")
    except Exception as e:
        print(f"[REST CLIENT ERROR] An error occurred: {e}")

if __name__ == "__main__":
    os.makedirs("logs", exist_ok=True)
    print("--- Starting Network Metrics Simulation ---")

    # 循环遍历所有预设场景
    while True:
        for name, duration, load_r, delay_r, loss_r in SCENARIOS[2:3]:
            print(f"\n[SCENARIO] Entering '{name}' for {duration} seconds...")
            start_time = time.time()

            # 在每个场景内持续生成指标，直到时间结束
            while time.time() - start_time < duration:
                metrics = get_metrics(load_r, delay_r, loss_r)
                # *** 关键修改：直接发送数据 ***
                send_metrics(metrics)

                # 打印当前场景和指标
                current_time = int(time.time() - start_time)
                print(
                    f"[{name}]   [{current_time}s/{duration}s] "
                    f"{{'traffic_load': {metrics['traffic_load'] * 100:.0f}%, "
                    f"'delay': {metrics['delay_ms']}ms, "
                    f"'packet_loss': {metrics['packet_loss'] * 100:.1f}%}}\n"
                )

                time.sleep(5) # 每 3 秒生成一次数据

        print("\n[SIMULATION] Looping back to the first scenario...")
