import requests
import json
import time
from flask import Flask, request, jsonify
from collections import deque

# 설정
RYU_REST_URL = "http://127.0.0.1:8080/qos/qos-policies"
HEADERS = {'Content-Type': 'application/json'}

app = Flask(__name__)

# Moving Average를 위한 큐 (최근 3개)
history_video_bps = deque(maxlen=3)

# --- QoS 상태 관리 클래스 ---
class QoSManager:
    def __init__(self):
        self.state = "IDLE"          # 상태: IDLE, ACTIVE, PROBING
        self.dl_bw_limit = 10        # 현재 다운로드 대역폭 제한 (기본 10)
        self.last_action_time = 0    # 마지막 QoS 동작 시간
        
        # Loss 지속 증가 확인용 히스토리
        self.loss_history = deque(maxlen=3)
        
        # [NEW] 가장 높았던 비디오 대역폭 기록용
        self.max_video_bw = 0.0
        
        # QoS 설정 상수
        self.MIN_BW = 1
        self.MAX_BW = 9.5  # QoS 해제 기준 (최소 360P 영상 품질 보장)
        self.PROBE_INTERVAL = 5 # 5초마다 BW 증가 시도

    def update(self, metrics):
        current_time = time.time()
        
        # metrics에서 데이터 추출
        # current_network.py에서 이동평균된 'video_loss_percent_ma'를 보냄
        loss_ma = metrics.get("video_loss_percent_ma", 0)
        vid_bps = metrics.get("video_mbps", 0)
        dl_bps = metrics.get("download_mbps", 0)
        avg_vid_bps = metrics.get("video_mbps_3sec_avg", 0)
        avg_dl_bps = metrics.get("download_mbps_3sec_avg", 0)
        total_bps = vid_bps + dl_bps
        
        # Loss 히스토리 업데이트
        self.loss_history.append(loss_ma)
        
        print(f"[ENGINE] State:{self.state} | DLBW:{self.dl_bw_limit}M | Loss(MA):{loss_ma}% | Vid:{vid_bps}M ({avg_vid_bps}Max:{self.max_video_bw}M)")

        # --- 상태 머신 로직 ---

        # 13. 트래픽 없음 (비디오 없음 OR 다운로드 없음) -> QoS OFF
        # - 비디오가 없으면 보호할 대상이 없음
        # - 다운로드가 없으면 혼잡을 유발하는 원인이 없음
        # -> 둘 중 하나라도 0.1Mbps 미만이면 QoS 불필요
        if vid_bps < 0.1 or dl_bps < 0.1:
            if self.state != "IDLE":
                print(">>> Traffic Missing (Video or Download). Reset QoS.")
                self.reset_qos()

            # [NEW] 비디오 트래픽이 멈췄으므로 Max BW 기록 초기화 (새로운 영상 재생 대비)
            if (vid_bps < 0.1):
                self.max_video_bw = 0.0
            return

        # [NEW] 비디오 최대 대역폭 갱신 (최근 3초 이동평균으로 최대값 측정)      
        if avg_vid_bps > self.max_video_bw:
            self.max_video_bw = avg_vid_bps

        # 5. 비디오 Loss 3초간 지속 증가 확인 (단순 증가세 또는 높은 Loss 지속)
        # 여기서는 Loss가 1% 이상인 상태가 3틱(3초) 유지되거나 증가하면 Trigger
        is_loss_increasing = False
        if len(self.loss_history) == 3:
            # 지속적으로 Loss가 있는 경우 (예: 1% 이상 지속)
            if all(l > 1.0 for l in self.loss_history):
                is_loss_increasing = True
        
        # [NEW] 가장 높았던 대역폭보다 10% 이상 떨어지는 경우 확인 (최근 3초 평균 비디오 대역폭이)
        is_bw_drop = False
        if self.max_video_bw > 0 and avg_vid_bps < (self.max_video_bw * 0.9):
            is_bw_drop = True

        # [NEW] QoS 개입 필요 여부 (Loss 증가 OR 대역폭 급감)
        need_qos_intervention = is_loss_increasing or is_bw_drop

        # === 상태별 동작 ===
        
        if self.state == "IDLE":
            # Loss 증가 또는 대역폭 10% 이상 하락 시 -> QoS 시작
            if need_qos_intervention:
                trigger_reason = "Loss Increasing" if is_loss_increasing else "BW Drop > 10%"
                print(f">>> {trigger_reason} Detected. QoS ON. Set Download BW = 5MB.")
                self.state = "ACTIVE"
                self.dl_bw_limit = 5 # 초기 진입 시 5Mbps로 제한 (강력 보호)
                self.apply_policy()
                self.last_action_time = current_time

        elif self.state == "ACTIVE":
            # 7. 여전히 상태가 나쁘면 BW 추가 감소
            if need_qos_intervention: 
                if self.dl_bw_limit > self.MIN_BW:
                    self.dl_bw_limit -= 1
                    print(f">>> Condition Bad. Decrease BW -> {self.dl_bw_limit}MB")
                    self.apply_policy()
                else:
                    print(">>> BW at Minimum (1MB). Maintaining.")
                self.last_action_time = current_time # 액션 취함
            
            # 8. Loss가 낮아짐 (안정화) -> 9. Probe 대기
            # Loss가 1 미만이고, 대역폭도 Max의 95% 이상으로 회복되었을 때 안정으로 판단
            # 또한 망이 최대로 사용되지 않아야 함 (총합 9Mbps 미만)
            elif loss_ma < 1 and (vid_bps > (self.max_video_bw * 0.95) and (total_bps < 9.0)): 
                if current_time - self.last_action_time >= self.PROBE_INTERVAL:
                    print(">>> Stable for 5s. Probing Bandwidth (+1MB)...")
                    self.state = "PROBING"
                    self.probe_bandwidth()

        elif self.state == "PROBING":
            # 10. Probe 후 Loss 다시 증가? -> 바로 복구
            # 즉각적인 반응을 위해 loss_ma > 2.0 and BW Drop
            if (loss_ma > 2.0) or is_bw_drop:
                print(">>> Probe Failed (Condition Bad). Reverting BW.")
                self.dl_bw_limit = max(self.MIN_BW, self.dl_bw_limit - 1)
                self.state = "ACTIVE"
                self.apply_policy()
                self.last_action_time = current_time
            
            # 11. 괜찮음 -> BW 계속 증가 시도
            else:
                # 다음 Probe 주기 확인은 main loop 주기에 따름 (여기선 바로 증가가 아니라 5초 주기)
                if current_time - self.last_action_time >= self.PROBE_INTERVAL:
                    if self.dl_bw_limit >= self.MAX_BW:
                        # 9.5MB 넘어가면 QoS OFF (360P 영상은 1Mbps로 품질 보장을 위해 9.5Mbps까지는 QoS 동작 필요)
                        # 하지만 +-1Mbps 단위로 조정하므로 실제로는 10Mbps 도달 시점에 해제
                        print(">>> DL BW > 9.5MB & Stable. QoS OFF.")
                        self.reset_qos()
                    else:
                        print(">>> Probing Success. Increasing BW...")
                        self.probe_bandwidth()

    def probe_bandwidth(self):
        self.dl_bw_limit += 1
        self.apply_policy()
        self.last_action_time = time.time()
        # Probe 상태 유지 (다음 틱에서 확인)

    def reset_qos(self):
        self.state = "IDLE"
        self.dl_bw_limit = 10 # 기본 링크 속도
        # Default 정책 전송
        policies = [
            {"name": "video", "priority": 20, "bandwidth-limit": 10},
            {"name": "download", "priority": 10, "bandwidth-limit": 10}
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