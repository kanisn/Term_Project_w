# decision_engine_push_to_ryu.py
import requests
import json, time, os
from flask import Flask, request, jsonify # 需要安装 Flask (pip install Flask)

# 根据指标决定策略 → 推送到 Ryu 的 REST API
# 它读取current_network.py (模拟器)生成的 latest_metrics.json 中的模拟指标，根据预设的阈值计算出新的 QoS 策略，然后通过 HTTP REST 请求将策略推送到 Ryu 控制器。
# 指向 Ryu REST 端点（WSGI 应用程序默认监听 8080 端口，除非 Ryu 配置已更改）
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
    # --- 定义所有策略的默认值 ---
    policies = {
        "video": {"name": "video", "priority": 7, "bandwidth-limit": 600},
        "download": {"name": "download", "priority": 5, "bandwidth-limit": 500},
        "background": {"name": "background", "priority": 1, "bandwidth-limit": 200},
    }

    if load > 1.1 or delay > 150 or loss > 0.02:
        # Scenario 3: Severe Congestion (Video + Download)
        # Objective: Absolutely protect the video, protect the download, and strictly limit the background.
        policies["video"]["priority"] = 200
        policies["video"]["bandwidth-limit"] = 800
        policies["download"]["bandwidth-limit"] = 500
        policies["background"]["bandwidth-limit"] = 50
        print("[DECISION] Severe Congestion: Increase video bandwidth, protect download, strictly limit others.")
    elif load > 0.85 or delay > 80 or loss > 0.005:
        # 场景 2: 高负载 (High Load - Video Streaming)
        # 目标: 保护 Video，适度限制 Download/Background
        policies["video"]["priority"] = 10
        policies["video"]["bandwidth-limit"] = 900
        policies["download"]["bandwidth-limit"] = 30
        policies["background"]["bandwidth-limit"] = 10
        print("[DECISION] High Load: Prioritize video, strictly limit other traffic.")
    else:
        # 场景 1 & 4: 正常或恢复 (Low/Moderate Load)
        # 目标: 宽松策略，提高资源利用率
        policies["video"]["priority"] = 7
        policies["video"]["bandwidth-limit"] = 800
        policies["download"]["bandwidth-limit"] = 600
        policies["background"]["bandwidth-limit"] = 300
        print("[DECISION] Normal Load: Relaxed policies for high utilization.")

    return list(policies.values())

def build_config_json(policies_list):
    # keep a container format similar to your YANG/RESTCONF
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


# --- Flask 应用 ---
app = Flask(__name__)

# 定义一个接收指标的 POST 路由（从current_network.py接收）
@app.route('/metrics', methods=['POST'])
def receive_metrics():
    if not request.is_json:
        return jsonify({"msg": "Missing JSON in request"}), 400

    metrics = request.get_json()

    # 1. 决策策略
    policies_to_push = decide_policy(metrics)

    # 2. 推送到 Ryu
    push_to_ryu(policies_to_push)

    return jsonify({"msg": "Policies updated successfully"}), 200


if __name__ == '__main__':
    # 在 127.0.0.1:5000 启动服务器
    print("--- Starting Decision Engine (REST Server) on port 5000 ---")
    app.run(host='127.0.0.1', port=5000)
