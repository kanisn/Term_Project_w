#!/usr/bin/env python3
import random, json, time, os
import requests
import sys

# --- 설정 ---
# 실시간 지표를 전송할 결정 엔진 REST 주소를 정의한다.
DECISION_ENGINE_URL = "http://127.0.0.1:5000/metrics"
HEADERS = {'Content-Type': 'application/json'}

# --- 네트워크 시나리오 정의 ---
# 각 튜플은 시나리오 이름, 지속 시간, 부하/지연/패킷 손실 범위를 의미한다.
SCENARIOS = [
    # 시나리오 1: 정상 부하
    ("Low Load (Normal)", 20, (0.4, 0.6), (20, 50), (0.001, 0.005)),
    # 시나리오 2: 비디오 트래픽 급증
    ("Video Surge (High Load)", 30, (0.85, 1.1), (80, 150), (0.005, 0.02)),
    # 시나리오 3: 비디오 + 대용량 다운로드로 인한 혼잡
    ("Congestion (Video + Download)", 40, (1.1, 1.4), (150, 250), (0.02, 0.05)),
    # 시나리오 4: 부하 감소 및 회복
    ("Recovery (Moderate Load)", 20, (0.6, 0.85), (50, 100), (0.003, 0.01)),
]


def get_metrics(load_range, delay_range, loss_range):
    """지정된 범위에서 임의 값을 뽑아 트래픽 부하, 지연, 손실률을 생성한다."""
    return {
        "traffic_load": round(random.uniform(*load_range), 2),
        "delay_ms": round(random.uniform(*delay_range), 1),
        "packet_loss": round(random.uniform(*loss_range), 3)
    }

def send_metrics(metrics):
    """metrics 딕셔너리를 JSON 으로 직렬화해 결정 엔진으로 POST 한다."""
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

    # 미리 정의된 시나리오를 순서대로 반복하며, 각 시나리오에서 연속적으로 지표를 전송한다.
    while True:
        for name, duration, load_r, delay_r, loss_r in SCENARIOS[2:3]:
            print(f"\n[SCENARIO] Entering '{name}' for {duration} seconds...")
            start_time = time.time()

            # 각 시나리오 안에서 지정된 시간 동안 연속적으로 지표를 생성한다.
            while time.time() - start_time < duration:
                metrics = get_metrics(load_r, delay_r, loss_r)
                # 측정값을 즉시 전송
                send_metrics(metrics)

                # 현재 시나리오와 지표를 출력
                current_time = int(time.time() - start_time)
                print(
                    f"[{name}]   [{current_time}s/{duration}s] "
                    f"{{'traffic_load': {metrics['traffic_load'] * 100:.0f}%, "
                    f"'delay': {metrics['delay_ms']}ms, "
                    f"'packet_loss': {metrics['packet_loss'] * 100:.1f}%}}\n"
                )

                time.sleep(5)

        print("\n[SIMULATION] Looping back to the first scenario...")
