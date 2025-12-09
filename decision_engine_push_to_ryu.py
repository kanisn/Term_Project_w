import requests
import json
import time
import csv
from datetime import datetime
from flask import Flask, request, jsonify
from collections import deque

# Configuration
RYU_REST_URL = "http://127.0.0.1:8080/qos/qos-policies"
HEADERS = {'Content-Type': 'application/json'}
LOG_CSV_FILE = "decision_engine_log.csv"
BW_OPTIMIZE_VALUE = 0.5  # Mbps
MAX_BANDWIDTH = 10.0  # Mbps

app = Flask(__name__)

# --- File initialization helpers ---
def init_csv():
    """Create the CSV header from scratch."""
    # Always start fresh (overwrite)
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


# --- QoS state manager ---
class QoSManager:
    def __init__(self):
        self.state = "IDLE"          # State: IDLE, ACTIVE
        self.dl_bw_limit = MAX_BANDWIDTH    # Current download bandwidth limit (default 10 Mbps)
        self.last_action_time = 0    # Last QoS action timestamp

        # History for detecting persistent loss increase
        self.loss_history = deque(maxlen=3)

        # QoS configuration constants
        self.MIN_BW = 1.0   # Minimum bandwidth limit (1 Mbps)
        self.MAX_BW = 9.5  # QoS deactivation threshold (keeps at least 360p quality)
        self.PROBE_INTERVAL = 3 # Attempt to increase bandwidth every 3 seconds

        self.max_vid_bps_avg = 0  # Maximum 10-second moving average video bandwidth

    def log_to_csv(self, timestamp, total_bps, vid_bps, dl_bps, qos_state, loss_ma, event_msg=""):
        """Append the current state to the CSV log."""
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
        qos_state = 0  # 0: IDLE, 1: ACTIVE

        # Extract values from metrics
        # current_network.py sends the moving average 'video_loss_percent_ma'
        loss_ma = metrics.get("video_loss_percent_ma", 0)
        loss = metrics.get("raw_loss_percent", 0)
        vid_bps = metrics.get("video_mbps", 0)
        dl_bps = metrics.get("download_mbps", 0)
        avg_vid_bps = metrics.get("video_mbps_10sec_avg", 0)
        avg_dl_bps = metrics.get("download_mbps_10sec_avg", 0)
        total_bps = vid_bps + dl_bps

        # Update loss history
        self.loss_history.append(loss)

        # Event message placeholder for logging
        event_msg = "-"

        print(f"[ENGINE] State:{self.state} | DLBW:{self.dl_bw_limit} Mbps | Loss(MA):{loss_ma}% | Vid:{vid_bps} Mbps (Vid_MAX:{self.max_vid_bps_avg} Mbps)")

        # Detect persistent video loss increase over ~3 seconds
        # Trigger if loss stays above 1% for three samples
        is_loss_increasing = False
        if len(self.loss_history) == 3:
            # Continuous loss present (e.g., consistently above 1%)
            if all(l > 1.0 for l in self.loss_history):
                is_loss_increasing = True

        # Detect more than 20% drop from the maximum 10-second moving average
        is_bw_drop = False
        if (self.max_vid_bps_avg < avg_vid_bps):
            self.max_vid_bps_avg = avg_vid_bps

        if (vid_bps < (self.max_vid_bps_avg * 0.8)) or ((vid_bps > 0) and dl_bps > 9.0):
            is_bw_drop = True

        # --- State machine ---
        # No traffic (no video OR no download) -> QoS OFF
        # - No video means there is nothing to protect
        # - No download means there is no congestion source
        # -> If either is below 0.1 Mbps, QoS is unnecessary
        if (total_bps < (MAX_BANDWIDTH / 2)):
            if self.state != "IDLE":
                print(">>> Traffic Missing (Video or Download). Reset QoS.")
                event_msg = "QoS OFF"
                qos_state = 0
                self.reset_qos()
            if vid_bps < 0.1:
                self.max_vid_bps_avg = 0  # Reset when there is no video traffic

            # Save log before returning
            self.log_to_csv(timestamp_str, total_bps, vid_bps, dl_bps, qos_state, loss_ma, event_msg)
            return
        else:
            # Determine whether QoS intervention is needed (loss increase OR bandwidth drop)
            need_qos_intervention = is_loss_increasing or is_bw_drop

        if self.state == "IDLE":
            # Start QoS when loss increases or bandwidth drops more than 20%
            if need_qos_intervention:
                trigger_reason = "Loss Increasing" if is_loss_increasing else "BW Drop > 20%"
                print(f">>> {trigger_reason} Detected. QoS ON. Set Download BW = 1 Mbps.")
                event_msg = "QoS ON (DL_BW=1Mbps)"
                self.state = "ACTIVE"
                qos_state = 1
                self.dl_bw_limit = self.MIN_BW  # Enter with a strict 1 Mbps limit
                self.apply_policy()
                self.last_action_time = current_time

        elif self.state == "ACTIVE":
            # Evaluate every probe interval
            if current_time - self.last_action_time >= self.PROBE_INTERVAL:
                if need_qos_intervention:
                    if self.dl_bw_limit > self.MIN_BW:
                        self.dl_bw_limit -= BW_OPTIMIZE_VALUE
                        print(f">>> Condition Bad. Decrease BW -> {self.dl_bw_limit} Mbps")
                        event_msg = "DL_BW Decreased"
                        self.apply_policy()
                    else:
                        print(">>> BW at Minimum (1 Mbps). Maintaining.")
                    # Reset timers after adjustments
                    self.last_action_time = current_time
                else:
                    if self.dl_bw_limit >= self.MAX_BW:
                        # Above 9.5 Mbps and stable -> turn QoS off
                        print(">>> DL BW > 9.5 Mbps & Stable. QoS OFF.")
                        event_msg = "QoS OFF"
                        qos_state = 0
                        self.reset_qos()
                        # Reset timers after adjustments
                        self.last_action_time = current_time
                    else:
                        # Increase only by the headroom left after video usage
                        if (self.dl_bw_limit < (MAX_BANDWIDTH - self.max_vid_bps_avg)):
                            print(">>> Probing Success. Increasing BW...")
                            event_msg = "DL_BW Increase"
                            self.probe_bandwidth()
                            # Reset timers after adjustments
                            self.last_action_time = current_time

        # Save log before returning
        self.log_to_csv(timestamp_str, total_bps, vid_bps, dl_bps, qos_state, loss_ma, event_msg)

    def probe_bandwidth(self):
        self.dl_bw_limit += BW_OPTIMIZE_VALUE
        self.apply_policy()
        self.last_action_time = time.time()
        # Maintain probe state for the next tick

    def reset_qos(self):
        self.state = "IDLE"
        self.dl_bw_limit = MAX_BANDWIDTH  # Default link speed
        # Send default policies
        policies = [
            {"name": "video", "priority": 20, "bandwidth-limit": MAX_BANDWIDTH},
            {"name": "download", "priority": 10, "bandwidth-limit": MAX_BANDWIDTH}
        ]
        self.push_to_ryu(policies)

    def apply_policy(self):
        # Adjust download (TCP) bandwidth
        policies = [
            # Protect video (higher priority)
            {"name": "video", "priority": 20, "bandwidth-limit": 9.0},
            # Apply current limit to download traffic
            {"name": "download", "priority": 10, "bandwidth-limit": self.dl_bw_limit},
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


# Instantiate manager
qos_manager = QoSManager()


@app.route('/metrics', methods=['POST'])
def handle_metrics():
    if not request.is_json:
        return jsonify({"error": "No JSON"}), 400

    metrics = request.get_json()
    # Delegate decision to the QoS manager
    qos_manager.update(metrics)

    return jsonify({"status": "processed"}), 200


if __name__ == '__main__':
    init_csv()  # Create CSV header at program start
    print("--- Decision Engine Started on Port 5000 ---")
    app.run(host='0.0.0.0', port=5000)
