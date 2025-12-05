import requests
import json
import time
import csv
import os
from datetime import datetime
from flask import Flask, request, jsonify
from collections import deque

# 설정
RYU_REST_URL = "http://127.0.0.1:8080/qos/qos-policies"
HEADERS = {'Content-Type': 'application/json'}
LOG_CSV_FILE = "decision_engine_log.csv"
BW_OPTIMIZE_VALUE = 0.5  # Mbps
MAX_BANDWIDTH = 10.0  # Mbps

app = Flask(__name__)

# --- 파일 초기화 함수 ---
def init_csv():
    """CSV 파일 헤더 생성"""
    # 항상 새로 시작 (덮어쓰기)
    with open(LOG_CSV_FILE, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            "hh:mm:ss", 
            "Total(Mbps)",
            "Video(Mbps)",
            "Download(Mbps)",
            "QoS On Flag", 
            "DL_BW_Limit(Mbps)", 
            "Video_Loss(%)",  
            "Event_Message"
        ])
    print(f"[INIT] Decision Engine Log initialized: {LOG_CSV_FILE}")

# --- QoS 상태 관리 클래스 ---
class QoSManager:
    def __init__(self):
        self.state = "IDLE"          # 상태: IDLE, ACTIVE
        self.dl_bw_limit = MAX_BANDWIDTH    # 현재 다운로드 대역폭 제한 (기본 10)
        self.last_action_time = 0    # 마지막 QoS 동작 시간
        
        # Loss 지속 증가 확인용 히스토리
        self.loss_history = deque(maxlen=3)
        
        # QoS 설정 상수
        self.MIN_BW = 1.0   # 최소 대역폭 제한 (1Mbps)
        self.MAX_BW = 9.5  # QoS 해제 기준 (최소 360P 영상 품질 보장)
        self.PROBE_INTERVAL = 3 # 5초마다 BW 증가 시도
        
        self.max_vid_bps_avg = 0  # 10초 이동평균 중 최대 비디오 대역폭

    def log_to_csv(self, timestamp, total_bps, vid_bps, dl_bps, qos_state, loss_ma, event_msg=""):
        """CSV에 현재 상태 한 줄 추가"""
        try:
            with open(LOG_CSV_FILE, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    timestamp,
                    round(total_bps, 2),
                    round(vid_bps, 2),
                    round(dl_bps, 2),
                    qos_state,
                    self.dl_bw_limit,
                    round(loss_ma, 2),
                    event_msg
                ])
        except Exception as e:
            print(f"[LOG ERROR] Could not write to CSV: {e}")

    def update(self, metrics):
        current_time = time.time()
        timestamp_str = datetime.now().strftime("%H:%M:%S")
        qos_state = 0;  # 0: IDLE, 1: ACTIVE
        # metrics에서 데이터 추출
        # current_network.py에서 이동평균된 'video_loss_percent_ma'를 보냄
        loss_ma = metrics.get("video_loss_percent_ma", 0)
        vid_bps = metrics.get("video_mbps", 0)
        dl_bps = metrics.get("download_mbps", 0)
        avg_vid_bps = metrics.get("video_mbps_10sec_avg", 0)
        avg_dl_bps = metrics.get("download_mbps_10sec_avg", 0)
        total_bps = vid_bps + dl_bps
        # Loss 히스토리 업데이트
        self.loss_history.append(loss_ma)
        
        # 로그용 이벤트 메시지 변수
        event_msg = "-"
        
        print(f"[ENGINE] State:{self.state} | DLBW:{self.dl_bw_limit}M | Loss(MA):{loss_ma}% | Vid:{vid_bps}M (Vid_MAX:{self.max_vid_bps_avg}M)")

        # 5. 비디오 Loss 3초간 지속 증가 확인 (높은 Loss 지속)
        # 여기서는 Loss가 1% 이상인 상태가 3초 유지 Trigger
        is_loss_increasing = False
        if len(self.loss_history) == 3:
            # 지속적으로 Loss가 있는 경우 (예: 1% 이상 지속)
            if all(l > 1.0 for l in self.loss_history):
                is_loss_increasing = True
        
        # [NEW] 이동평균 10초 중 최대 대역폭보다 20%(8->6 25%) 이상 떨어지는 경우 확인
        is_bw_drop = False
        if (self.max_vid_bps_avg < avg_vid_bps):
            self.max_vid_bps_avg = avg_vid_bps
        if vid_bps < (self.max_vid_bps_avg * 0.8):
            is_bw_drop = True
            
        # --- 상태 머신 로직 ---
        # 13. 트래픽 없음 (비디오 없음 OR 다운로드 없음) -> QoS OFF
        # - 비디오가 없으면 보호할 대상이 없음
        # - 다운로드가 없으면 혼잡을 유발하는 원인이 없음
        # -> 둘 중 하나라도 0.1Mbps 미만이면 QoS 불필요
        if vid_bps < 0.1 or dl_bps < 0.1:
            if self.state != "IDLE":
                print(">>> Traffic Missing (Video or Download). Reset QoS.")
                event_msg = "QoS OFF"
                qos_state = 0
                self.reset_qos()
            if vid_bps < 0.1:
                self.max_vid_bps_avg = 0 # 비디오 트래픽이 없으면 0으로 설정

            # 리턴 전 로그 저장
            self.log_to_csv(timestamp_str, total_bps, vid_bps, dl_bps, qos_state, loss_ma, event_msg)
            return


        # [NEW] QoS 개입 필요 여부 (Loss 증가 OR 대역폭 급감)
        need_qos_intervention = is_loss_increasing or is_bw_drop

        # === 상태별 동작 ===
        
        if self.state == "IDLE":
            # Loss 증가 또는 대역폭 20% 이상 하락 시 -> QoS 시작
            if need_qos_intervention:
                trigger_reason = "Loss Increasing" if is_loss_increasing else "BW Drop > 20%"
                print(f">>> {trigger_reason} Detected. QoS ON. Set Download BW = 1MB.")
                event_msg = "QoS ON (DL_BW=1MB)"
                self.state = "ACTIVE"                
                qos_state = 1
                self.dl_bw_limit = self.MIN_BW # 초기 진입 시 1Mbps로 제한 (강력 보호)
                self.apply_policy()
                self.last_action_time = current_time

        elif self.state == "ACTIVE":
            # 3초 주기 결과 평가
            if current_time - self.last_action_time >= self.PROBE_INTERVAL:
                if need_qos_intervention:
                    if self.dl_bw_limit > self.MIN_BW:
                        self.dl_bw_limit -= BW_OPTIMIZE_VALUE
                        print(f">>> Condition Bad. Decrease BW -> {self.dl_bw_limit}MB")
                        event_msg = "DL_BW Decreased"
                        self.apply_policy()
                    else:
                        print(">>> BW at Minimum (1MB). Maintaining.")            
                    # 조정 후 타이머/카운터 리셋
                    self.last_action_time = current_time
                else:
                    if self.dl_bw_limit >= self.MAX_BW:
                        # 9.5MB 넘어가면 QoS OFF (360P 영상은 1Mbps로 품질 보장을 위해 9.5Mbps까지는 QoS 동작 필요)
                        print(">>> DL BW > 9.5MB & Stable. QoS OFF.")
                        event_msg = "QoS OFF" 
                        qos_state = 0
                        self.reset_qos()         
                        # 조정 후 타이머/카운터 리셋
                        self.last_action_time = current_time
                    else:
                        # 최대 대역폭에서 비디오 사용량을 제외한 만큼만 올릴 수 있다.
                        if (self.dl_bw_limit < (MAX_BANDWIDTH - self.max_vid_bps_avg)):
                            print(">>> Probing Success. Increasing BW...")
                            event_msg = "DL_BW Increase"
                            self.probe_bandwidth() 
                            # 조정 후 타이머/카운터 리셋
                            self.last_action_time = current_time   
        
        # 리턴 전 로그 저장
        self.log_to_csv(timestamp_str, total_bps, vid_bps, dl_bps, qos_state, loss_ma, event_msg)

    def probe_bandwidth(self):
        self.dl_bw_limit += BW_OPTIMIZE_VALUE
        self.apply_policy()
        self.last_action_time = time.time()
        # Probe 상태 유지 (다음 틱에서 확인)

    def reset_qos(self):
        self.state = "IDLE"
        self.dl_bw_limit = MAX_BANDWIDTH # 기본 링크 속도
        # Default 정책 전송
        policies = [
            {"name": "video", "priority": 20, "bandwidth-limit": MAX_BANDWIDTH},
            {"name": "download", "priority": 10, "bandwidth-limit": MAX_BANDWIDTH}
        ]
        self.push_to_ryu(policies)

    def apply_policy(self):
        # 6. 다운로드 트래픽(TCP) BW 조정
        policies = [
            # Video는 보호 (우선순위 높임)
            {"name": "video", "priority": 20, "bandwidth-limit": 9.0},
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
    init_csv()  # 프로그램 시작 시 CSV 헤더 작성
    print("--- Decision Engine Started on Port 5000 ---")
    app.run(host='0.0.0.0', port=5000)