#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
import threading
import time
from typing import Callable, Optional

try:
    # Local log store used by the web backend when available
    from log_utils import log_store  # type: ignore
except Exception:  # pragma: no cover - fallback for standalone CLI use
    log_store = None

# --- Configuration ---
# Note: This script uses TCP, so the receiver (h1) must be running
# 'iperf -s -p 5001' in TCP mode (without the UDP -u option).
TARGET_IP = "10.0.0.1"
TARGET_PORT = 5001

# Bitrate settings per quality (resolution, bitrate in bps)
QUALITIES = [
    ("360p (Low)",   1_000_000),  # 1 Mbps
    ("480p (SD)",    2_500_000),  # 2.5 Mbps
    ("720p (HD)",    5_000_000),  # 5 Mbps
    ("1080p (FHD)",  8_000_000)   # 8 Mbps (max)
]


def _log(line: str) -> None:
    print(line)
    if log_store:
        log_store.append("traffic_video_abr", line)


def run_abr_simulation(
    duration_sec: int = 60,
    stop_event: Optional[threading.Event] = None,
    target_ip: str = TARGET_IP,
    target_port: int = TARGET_PORT,
):
    _log(f"\n[ABR] Connecting to Video Receiver at {target_ip}:{target_port} (TCP)...")

    try:
        # Create and connect TCP socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((target_ip, target_port))
    except Exception as e:
        _log(f"[ERROR] Connection Failed: {e}")
        _log("Tip: Ensure 'iperf -s -p 5001' is running on host h1 (without UDP -u option).")
        return

    _log("[ABR] Connection Established. Starting Adaptive Streaming...")
    _log("------------------------------------------------------------------")
    _log(f"{'Time':<8} | {'Quality':<12} | {'Bitrate':<10} | {'TxTime':<8} | {'Status'}")
    _log("------------------------------------------------------------------")

    start_time = time.time()
    current_idx = 3  # Start by attempting the highest quality (1080p)

    # Simulation loop for the specified duration
    while (time.time() - start_time) < duration_sec:
        if stop_event and stop_event.is_set():
            _log("[ABR] Stop requested. Closing connection.")
            break
        quality_name, bitrate = QUALITIES[current_idx]

        # 1. Calculate the payload size for one second of video (bytes)
        # TCP streams data regardless of packet boundaries
        chunk_size = int(bitrate / 8)
        payload = b'x' * chunk_size

        # 2. Transmit and measure time
        chunk_start = time.time()
        try:
            sock.sendall(payload)  # Blocks until buffer is accepted (reflects network speed)
        except BrokenPipeError:
            _log("[ERROR] Connection closed by remote host.")
            break

        chunk_end = time.time()
        tx_time = chunk_end - chunk_start

        # 3. ABR decision logic (quality adjustment)
        status = "Stable"

        # (A) Congestion: sending 1 second of video takes longer than 1 second -> risk of buffering
        if tx_time > 1.0:
            if current_idx > 0:
                current_idx -= 1
                status = "DOWNGRADE (Congestion)"
            else:
                status = "Buffering (Min Quality)"

        # (B) Plenty of headroom: sending 1 second of video in under 0.8 seconds -> safe to upgrade
        elif tx_time < 0.8:
            if current_idx < len(QUALITIES) - 1:
                current_idx += 1
                status = "UPGRADE (Clear)"

        # Log output
        elapsed_total = int(time.time() - start_time)
        _log(f"{elapsed_total}s      | {quality_name:<12} | {bitrate/1e6:.1f}M      | {tx_time:.2f}s    | {status}")

        # 4. Pacing: if we sent too fast, sleep the remainder to mimic real-time playback
        # Example: if 1 second of video sent in 0.5 sec, rest for 0.5 sec (TCP flow control)
        sleep_time = max(0, 1.0 - tx_time)
        time.sleep(sleep_time)

    _log("\n[ABR] Streaming Finished.")
    sock.close()


class VideoTrafficController:
    """Background controller used by the web backend to manage ABR traffic."""

    def __init__(self, target_host: str = TARGET_IP, target_port: int = TARGET_PORT, run_host_label: str = "vSvr") -> None:
        self.target_host = target_host
        self.target_port = target_port
        self.run_host_label = run_host_label
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, duration_sec: int = 3600) -> None:
        if self.is_running:
            _log("[ABR] Stream already running on vSvr.")
            return

        self._stop_event = threading.Event()
        _log(f"[ABR] Launching adaptive stream from {self.run_host_label} targeting {self.target_host}:{self.target_port}.")

        def _worker() -> None:
            run_abr_simulation(
                duration_sec=duration_sec,
                stop_event=self._stop_event,
                target_ip=self.target_host,
                target_port=self.target_port,
            )

        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self.is_running:
            _log("[ABR] No active stream to stop.")
            return
        assert self._stop_event is not None
        _log("[ABR] Stop requested for adaptive stream.")
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)


controller = VideoTrafficController()


if __name__ == "__main__":
    try:
        dur = input("Enter playback duration (seconds) (default 60): ")
        dur = int(dur) if dur.strip() else 60
        run_abr_simulation(dur)
    except KeyboardInterrupt:
        print("\n[STOP] User interrupted.")
