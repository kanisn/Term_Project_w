#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import threading
from typing import Optional

try:
    from log_utils import log_store  # type: ignore
except Exception:  # pragma: no cover - used when backend is not running
    log_store = None

# --- Configuration ---
# IP of h2 (Download User) in the Mininet topology
TARGET_IP = "10.0.0.2"
TARGET_PORT = 5002


def print_menu():
    log("\n===========================================")
    log("    DOWNLOAD Traffic Generator (dSrv)      ")
    log("===========================================")
    log(f"Target: {TARGET_IP}:{TARGET_PORT} (TCP)")
    log("Simulates large file downloads.")
    log("-------------------------------------------")
    log("1. Burst download (max speed, run for set time)")
    log("2. Continuous download (max speed until stopped)")
    log("0. Exit")
    log("===========================================")


def log(message: str) -> None:
    print(message)
    if log_store:
        log_store.append("traffic_file", message)


def run_simulation():
    while True:
        print_menu()
        choice = input("Select menu >> ").strip()

        if choice == '0':
            print("Exiting program.")
            break

        # --- Option 1: Timed download ---
        if choice == '1':
            try:
                dur = input("Enter transfer duration (seconds) (default 30): ").strip()
                if dur == "":
                    dur = "30"
                if not dur.isdigit():
                    log("Please enter a number.")
                    continue
                # Use -P 10 to create 10 parallel connections for aggressive bandwidth usage
                log(f"\n[INFO] Starting aggressive download... ({dur} sec, 10 parallel connections)")
                # -c: Client, -p: Port, -t: Time, -i: Interval, -P: Parallel streams
                os.system(f"iperf -c {TARGET_IP} -p {TARGET_PORT} -t {dur} -P 10 -i 1")

            except KeyboardInterrupt:
                log("\n\n[STOP] Stopping download.")
            except Exception as e:
                log(f"[ERROR] {e}")

        # --- Option 2: Continuous download ---
        elif choice == '2':
            try:
                log(f"\n[INFO] Starting continuous download (max TCP speed)")
                log("[INFO] Press Ctrl+C to stop and return to the menu.")

                # Loop until user stops manually
                while True:
                    # Use -P 10 for multiple parallel flows
                    cmd = f"iperf -c {TARGET_IP} -p {TARGET_PORT} -t 5 -P 10 -i 1"
                    os.system(cmd)

            except KeyboardInterrupt:
                log("\n\n[STOP] Continuous download stopped.")
            except Exception as e:
                log(f"[ERROR] {e}")

        else:
            log("Invalid selection.")

        input("Press Enter to return to the menu...")


if __name__ == "__main__":
    run_simulation()


class DownloadTrafficController:
    """Controller used by the web backend to run downloads from dSvr."""

    def __init__(self, target_ip: str = TARGET_IP, target_port: int = TARGET_PORT, run_host_label: str = "dSvr") -> None:
        self.target_ip = target_ip
        self.target_port = target_port
        self.run_host_label = run_host_label
        self._stop_event: Optional[threading.Event] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            log("[DOWNLOAD] Traffic already running on dSvr.")
            return
        self._stop_event = threading.Event()
        log(f"[DOWNLOAD] Launching aggressive download traffic from {self.run_host_label} targeting {self.target_ip}:{self.target_port}.")

        def _worker() -> None:
            try:
                # Keep issuing iperf jobs until stopped
                while self._stop_event and not self._stop_event.is_set():
                    cmd = f"iperf -c {self.target_ip} -p {self.target_port} -t 5 -P 10 -i 1"
                    os.system(cmd)
            finally:
                log("[DOWNLOAD] Background traffic loop finished.")

        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self.is_running:
            log("[DOWNLOAD] No active download traffic to stop.")
            return
        assert self._stop_event is not None
        log("[DOWNLOAD] Stop requested for download traffic.")
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)


controller = DownloadTrafficController()
