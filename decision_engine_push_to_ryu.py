# decision_engine_push_to_ryu.py
import requests
import json, time, os
from flask import Flask, request, jsonify  # Flask가 필요하다 (pip install Flask)

# 네트워크 지표를 바탕으로 QoS 정책을 계산하여 Ryu REST API(기본 8080)로 전달한다.
RYU_REST_URL = "http://127.0.0.1:8080/qos-policies"
HEADERS = {'Content-Type': 'application/json', 'Accept': 'application/json'}

def decide_policy(metrics):
    load = metrics["traffic_load"]
    delay = metrics["delay_ms"]
    loss = metrics["packet_loss"]

    print("---------------------------------------------------\n"
        f"traffic_load: {load * 100:.0f}%, "
        f"delay: {delay}ms, "
        f"packet_loss: {loss * 100:.1f}%"
    )
    # 기본 정책을 정의하고 이후 상황에 맞춰 priority/bandwidth 를 조정한다.
    policies = {
        "video": {"name": "video", "priority": 7, "bandwidth-limit": 600},
        "download": {"name": "download", "priority": 5, "bandwidth-limit": 500},
        "background": {"name": "background", "priority": 1, "bandwidth-limit": 200},
    }

    if load > 1.1 or delay > 150 or loss > 0.02:
        # 심각한 혼잡: 비디오와 다운로드를 최대한 보호하고 배경 트래픽을 강하게 제한한다.
        policies["video"]["priority"] = 200
        policies["video"]["bandwidth-limit"] = 800
        policies["download"]["bandwidth-limit"] = 500
        policies["background"]["bandwidth-limit"] = 50
        print("[DECISION] Severe Congestion: Increase video bandwidth, protect download, strictly limit others.")
    elif load > 0.85 or delay > 80 or loss > 0.005:
        # 높은 부하에서는 비디오를 보호하고, 나머지 트래픽은 엄격히 제한한다.
        policies["video"]["priority"] = 10
        policies["video"]["bandwidth-limit"] = 900
        policies["download"]["bandwidth-limit"] = 30
        policies["background"]["bandwidth-limit"] = 10
        print("[DECISION] High Load: Prioritize video, strictly limit other traffic.")
    else:
        # 평상시에는 자원을 적극 활용하기 위해 더 완화된 값을 사용한다.
        policies["video"]["priority"] = 7
        policies["video"]["bandwidth-limit"] = 800
        policies["download"]["bandwidth-limit"] = 600
        policies["background"]["bandwidth-limit"] = 300
        print("[DECISION] Normal Load: Relaxed policies for high utilization.")

    return list(policies.values())

def build_config_json(policies_list):
    # YANG 모델을 따라 "qos-policies" 컨테이너 구조로 JSON 을 감싼다.
    data = {
        "qos-policies:qos-policies": {
            "policy": policies_list
        }
    }
    return json.dumps(data)

def push_to_ryu(policies_list):
    json_config = build_config_json(policies_list)
    try:
        r = requests.put(RYU_REST_URL, data=json_config, headers=HEADERS, timeout=3)
        if r.status_code in (200, 204):
            print(f"[RYU REST] Successfully sent {len(policies_list)} policies (status {r.status_code}).")
        else:
            print(f"[RYU REST][ERROR] Status: {r.status_code}, Response: {r.text}")
    except Exception as e:
        print("[RYU REST][ERROR] Failed to connect to Ryu:", e)


app = Flask(__name__)

@app.route('/metrics', methods=['POST'])
def receive_metrics():
    if not request.is_json:
        return jsonify({"msg": "Missing JSON in request"}), 400

    metrics = request.get_json()

    # 1. 정책 결정
    policies_to_push = decide_policy(metrics)

    # 2. Ryu 로 전송
    push_to_ryu(policies_to_push)

    return jsonify({"msg": "Policies updated successfully"}), 200


if __name__ == '__main__':
    print("--- Starting Decision Engine (REST Server) on port 5000 ---")
    app.run(host='127.0.0.1', port=5000)
