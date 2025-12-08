import csv
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List

from flask import Flask, jsonify, render_template, send_from_directory

from log_utils import log_store
from traffic_file import controller as download_controller
from traffic_video_abr import controller as video_controller

BASE_DIR = Path(__file__).parent
NETWORK_CSV = BASE_DIR / "network_traffic.csv"
DECISION_ENGINE_CSV = BASE_DIR / "decision_engine_log.csv"

app = Flask(__name__, static_folder="static", template_folder="templates")

SCRIPT_MAP: Dict[str, Path] = {
    "qos_ryu_app": BASE_DIR / "qos_ryu_app.py",
    "mininet_topo": BASE_DIR / "mininet_topo.py",
    "decision_engine_push_to_ryu": BASE_DIR / "decision_engine_push_to_ryu.py",
    "current_network": BASE_DIR / "current_network.py",
}

processes: Dict[str, subprocess.Popen] = {}
process_threads: Dict[str, threading.Thread] = {}
state_flags = {
    "video_ready": False,
    "download_ready": False,
}


def _read_process_output(name: str, proc: subprocess.Popen) -> None:
    for line in proc.stdout or []:
        log_store.append(name, line.rstrip("\n"))
    proc.wait()
    log_store.append(name, f"[SYSTEM] {name} stopped with code {proc.returncode}.")


def _start_process(name: str) -> Dict[str, str]:
    if name not in SCRIPT_MAP:
        return {"error": "Unknown script."}
    existing = processes.get(name)
    if existing and existing.poll() is None:
        return {"status": "running"}

    proc = subprocess.Popen(
        ["python3", str(SCRIPT_MAP[name])],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    processes[name] = proc
    reader = threading.Thread(target=_read_process_output, args=(name, proc), daemon=True)
    process_threads[name] = reader
    reader.start()
    log_store.append(name, f"[SYSTEM] Started {name}.")
    return {"status": "started"}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/static/<path:path>")
def serve_static(path: str):
    return send_from_directory(app.static_folder, path)


@app.route("/api/run/<script>", methods=["POST"])
def run_script(script: str):
    # Special handling for traffic controllers that run on vSvr/dSvr
    if script == "traffic_video_abr":
        state_flags["video_ready"] = True
        log_store.append(script, "[SYSTEM] Video traffic controller ready on vSvr.")
        return jsonify({"status": "ready"})
    if script == "traffic_file":
        state_flags["download_ready"] = True
        log_store.append(script, "[SYSTEM] Download traffic controller ready on dSvr.")
        return jsonify({"status": "ready"})

    result = _start_process(script)
    return jsonify(result)


@app.route("/api/logs/<channel>")
def get_logs(channel: str):
    return jsonify({"logs": log_store.get_logs(channel)})


@app.route("/api/video/start", methods=["POST"])
def start_video():
    video_controller.start()
    return jsonify({"status": "started"})


@app.route("/api/video/stop", methods=["POST"])
def stop_video():
    video_controller.stop()
    return jsonify({"status": "stopped"})


@app.route("/api/download/start", methods=["POST"])
def start_download():
    download_controller.start()
    return jsonify({"status": "started"})


@app.route("/api/download/stop", methods=["POST"])
def stop_download():
    download_controller.stop()
    return jsonify({"status": "stopped"})


@app.route("/api/qos-status")
def qos_status():
    qos_proc = processes.get("qos_ryu_app")
    status = "ACTIVE" if qos_proc and qos_proc.poll() is None else "IDLE"
    return jsonify({"status": status})


@app.route("/api/traffic")
def traffic_metrics():
    data = _read_recent_traffic(NETWORK_CSV, limit=60)
    totals = [row["total_mbps"] for row in data]
    video = [row["video_mbps"] for row in data]
    download = [row["download_mbps"] for row in data]
    timestamps = [row["timestamp"] for row in data]
    return jsonify(
        {
            "timestamps": timestamps,
            "total": totals,
            "video": video,
            "download": download,
        }
    )


@app.route("/api/decision-log")
def decision_log():
    log_rows = []
    if DECISION_ENGINE_CSV.exists():
        with DECISION_ENGINE_CSV.open("r", newline="") as csvfile:
            reader = csv.reader(csvfile)
            for row in reader:
                log_rows.append(row)
    return jsonify({"rows": log_rows})


@app.route("/api/state-flags")
def state_flags_view():
    return jsonify(state_flags)


def _read_recent_traffic(csv_path: Path, limit: int = 60) -> List[Dict[str, float]]:
    if not csv_path.exists():
        return []
    rows: List[Dict[str, float]] = []
    with csv_path.open("r") as fh:
        lines = fh.readlines()[-limit:]
    for line in lines:
        if line.startswith("hh:mm:ss") or not line.strip():
            continue
        try:
            hhmmss, total, video, download, *_rest = line.strip().split(",")
            rows.append(
                {
                    "timestamp": hhmmss,
                    "total_mbps": float(total),
                    "video_mbps": float(video),
                    "download_mbps": float(download),
                }
            )
        except ValueError:
            continue
    return rows


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"Starting dashboard on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
