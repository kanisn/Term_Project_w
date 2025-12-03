import requests
import json
from flask import Flask, request, jsonify

# Ryu Controller URL
RYU_REST_URL = "http://127.0.0.1:8080/qos/qos-policies"
HEADERS = {'Content-Type': 'application/json'}

app = Flask(__name__)

def decide_policy(metrics):
    load = metrics.get("traffic_load", 0)
    delay = metrics.get("delay_ms", 0)
    loss = metrics.get("packet_loss", 0)
    video_bw = metrics.get("video_mbps", 0)

    print(f"[ENGINE] Analyzing: Load={load}Mbps, Delay={delay}ms, Loss={loss:.1%}")

    # 기본 정책 (평상시)
    # 10Mbps 링크를 공유하므로 합이 10을 넘으면 안됨
    policies = {
        "video": {"name": "video", "priority": 10, "bandwidth-limit": 8},
        "download": {"name": "download", "priority": 5, "bandwidth-limit": 8},
        "background": {"name": "background", "priority": 1, "bandwidth-limit": 2}
    }

    # --- 상황별 정책 조정 ---
    
    # 1. 심각한 혼잡 (손실 발생 OR 지연 150ms 이상 OR 로드 9.5M 이상)
    if loss > 0.01 or delay > 150 or load > 9.5:
        print(">>> CRITICAL CONGESTION! Throttling Download.")
        
        # 비디오 최우선 보호
        policies["video"]["priority"] = 50
        policies["video"]["bandwidth-limit"] = 9 # 링크 거의 전체 할당
        
        # 다운로드 강력 제한 (비디오를 위해 희생)
        policies["download"]["priority"] = 1
        policies["download"]["bandwidth-limit"] = 1 # 1Mbps로 제한
        
    # 2. 주의 단계 (로드 8M 이상)
    elif load > 8.0:
        print(">>> HIGH LOAD. Balancing traffic.")
        policies["video"]["priority"] = 20
        policies["video"]["bandwidth-limit"] = 7
        policies["download"]["priority"] = 10
        policies["download"]["bandwidth-limit"] = 3
        
    else:
        print(">>> NETWORK STABLE. Relaxing limits.")

    return list(policies.values())

def push_to_ryu(policies):
    payload = {
        "qos-policies:qos-policies": {
            "policy": policies
        }
    }
    try:
        r = requests.put(RYU_REST_URL, json=payload, headers=HEADERS, timeout=2)
        if r.status_code == 200:
            print("[RYU] Policy Updated Successfully.")
        else:
            print(f"[RYU ERROR] {r.status_code} {r.text}")
    except Exception as e:
        print(f"[RYU FAIL] {e}")

@app.route('/metrics', methods=['POST'])
def handle_metrics():
    if not request.is_json:
        return jsonify({"error": "No JSON"}), 400
    
    metrics = request.get_json()
    new_policies = decide_policy(metrics)
    push_to_ryu(new_policies)
    
    return jsonify({"status": "processed"}), 200

if __name__ == '__main__':
    print("--- Decision Engine Started on Port 5000 ---")
    app.run(host='0.0.0.0', port=5000)