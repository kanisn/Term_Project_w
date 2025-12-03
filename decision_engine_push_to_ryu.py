import requests
import json
import time
from flask import Flask, request, jsonify
from collections import deque

# 설정
RYU_REST_URL = "http://127.0.0.1:8080/qos/qos-policies"
HEADERS = {'Content-Type': 'application/json'}

app = Flask(__name__)

# --- QoS 상태 관리 클래스 ---
class QoSManager:
    def __init__(self):
        self.state = "IDLE"          # 상태: IDLE, ACTIVE, PROBING
        self.dl_bw_limit = 10        # 현재 다운로드 대역폭 제한 (기본 10)
        self.last_action_time = 0    # 마지막 QoS 동작 시간
        
        # Loss 지속 증가 확인용 히스토리
        self.loss_history = deque(maxlen=3)
        
        # QoS 설정 상수
        self.MIN_BW = 1
        self.MAX_BW = 5  # QoS 해제 기준
        self.PROBE_INTERVAL = 5 # 5초마다 BW 증가 시도

    def update(self, metrics):
        current_time = time.time()
        
        # metrics에서 데이터 추출
        # current_network.py에서 이동평균된 'video_loss_percent_ma'를 보냄
        loss_ma = metrics.get("video_loss_percent_ma", 0)
        vid_bps = metrics.get("video_mbps", 0)
        dl_bps = metrics.get("download_mbps", 0)
        
        # Loss 히스토리 업데이트
        self.loss_history.append(loss_ma)
        
        print(f"[ENGINE] State:{self.state} | BW:{self.dl_bw_limit}M | Loss(MA):{loss_ma}% | Vid:{vid_bps}M Dl:{dl_bps}M")

        # --- 상태 머신 로직 ---

        # 13. 트래픽 없음 (비디오 없음 OR 다운로드 없음) -> QoS OFF
        # - 비디오가 없으면 보호할 대상이 없음
        # - 다운로드가 없으면 혼잡을 유발하는 원인이 없음
        # -> 둘 중 하나라도 0.1Mbps 미만이면 QoS 불필요
        if vid_bps < 0.1 or dl_bps < 0.1:
            if self.state != "IDLE":
                print(">>> Traffic Missing (Video or Download). Reset QoS.")
                self.reset_qos()
            return

        # 5. 비디오 Loss 3초간 지속 증가 확인 (단순 증가세 또는 높은 Loss 지속)
        # 여기서는 Loss가 1% 이상인 상태가 3틱(3초) 유지되거나 증가하면 Trigger
        is_loss_increasing = False
        if len(self.loss_history) == 3:
            # 지속적으로 Loss가 있는 경우 (예: 2%, 2.5%, 3% or 5%, 5%, 5%)
            if all(l > 1.0 for l in self.loss_history):
                is_loss_increasing = True

        # === 상태별 동작 ===
        
        if self.state == "IDLE":
            # Loss 증가 감지 -> QoS 시작
            if is_loss_increasing:
                print(">>> Loss Detected (3 sec). QoS ON. Set Download BW = 5MB.")
                self.state = "ACTIVE"
                self.dl_bw_limit = 5
                self.apply_policy()
                self.last_action_time = current_time

        elif self.state == "ACTIVE":
            # 7. Loss가 유지되거나 증가하면 BW 감소
            if is_loss_increasing: # 여전히 Loss가 높음
                if self.dl_bw_limit > self.MIN_BW:
                    self.dl_bw_limit -= 1
                    print(f">>> Loss Persists. Decrease BW -> {self.dl_bw_limit}MB")
                    self.apply_policy()
                else:
                    print(">>> BW at Minimum (1MB). Maintaining.")
                self.last_action_time = current_time # 액션 취함
            
            # 8. Loss가 낮아짐 (안정화) -> 9. Probe 대기
            elif loss_ma < 0.5: # Loss가 거의 없음
                if current_time - self.last_action_time >= self.PROBE_INTERVAL:
                    print(">>> Stable for 5s. Probing Bandwidth (+1MB)...")
                    self.state = "PROBING"
                    self.probe_bandwidth()

        elif self.state == "PROBING":
            # 10. Probe 후 Loss 다시 증가? -> 바로 복구
            if loss_ma > 1.0:
                print(">>> Probe Failed (Loss increased). Reverting BW.")
                self.dl_bw_limit = max(self.MIN_BW, self.dl_bw_limit - 1)
                self.state = "ACTIVE"
                self.apply_policy()
                self.last_action_time = current_time
            
            # 11. Loss 안 오름 -> BW 계속 증가 시도
            else:
                # 다음 Probe 주기 확인은 main loop 주기에 따름 (여기선 바로 증가가 아니라 5초 주기)
                if current_time - self.last_action_time >= self.PROBE_INTERVAL:
                    if self.dl_bw_limit >= self.MAX_BW:
                        # 5MB 넘어가면 QoS OFF
                        print(">>> BW > 5MB & Stable. QoS OFF.")
                        self.reset_qos()
                    else:
                        print(">>> Probing Success. Increasing BW...")
                        self.probe_bandwidth()

    def probe_bandwidth(self):
        self.dl_bw_limit += 1
        self.apply_policy()
        self.last_action_time = time.time()
        # Probe 상태 유지 (다음 틱에서 loss 확인)

    def reset_qos(self):
        self.state = "IDLE"
        self.dl_bw_limit = 10 # 기본 링크 속도
        # Default 정책 전송
        policies = [
            {"name": "video", "priority": 10, "bandwidth-limit": 10},
            {"name": "download", "priority": 5, "bandwidth-limit": 10}
        ]
        self.push_to_ryu(policies)

    def apply_policy(self):
        # 6. 다운로드 트래픽(TCP) BW 조정
        policies = [
            # Video는 보호 (우선순위 높임)
            {"name": "video", "priority": 20, "bandwidth-limit": 9},
            # Download는 현재 Limit 적용
            {"name": "download", "priority": 10, "bandwidth-limit": self.dl_bw_limit}
        ]
        self.push_to_ryu(policies)

    def push_to_ryu(self, policies):
        payload = {
            "qos-policies:qos-policies": {
                "policy": policies
            }
        }
        try:
            r = requests.put(RYU_REST_URL, json=payload, headers=HEADERS, timeout=1)
            if r.status_code != 200:
                print(f"[RYU ERROR] {r.text}")
        except Exception as e:
            print(f"[RYU FAIL] {e}")

# 인스턴스 생성
qos_manager = QoSManager()

@app.route('/metrics', methods=['POST'])
def handle_metrics():
    if not request.is_json:
        return jsonify({"error": "No JSON"}), 400
    
    metrics = request.get_json()
    # QoS 매니저에게 판단 위임
    qos_manager.update(metrics)
    
    return jsonify({"status": "processed"}), 200

if __name__ == '__main__':
    print("--- Decision Engine Started on Port 5000 ---")
    app.run(host='0.0.0.0', port=5000)