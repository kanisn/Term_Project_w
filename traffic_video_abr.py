#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
import time

# --- Configuration ---
# Note: This script uses TCP, so the receiver (h1) must be running
# 'iperf -s -p 5001' in TCP mode (without the UDP -u option).
TARGET_IP = "10.0.0.1"
TARGET_PORT = 5001

# Bitrate settings per quality (resolution, bitrate in bps)
QUALITIES = [
    ("360p (Low)",   1_000_000),  # 1 Mbps
    ("480p (SD)",    3_000_000),  # 3 Mbps
    ("720p (HD)",    6_000_000),  # 6 Mbps
    ("1080p (FHD)",  8_000_000)   # 8 Mbps (max)
]


def run_abr_simulation(duration_sec=60):
    print(f"\n[ABR] Connecting to Video Receiver at {TARGET_IP}:{TARGET_PORT} (TCP)...")

    try:
        # Create and connect TCP socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((TARGET_IP, TARGET_PORT))
    except Exception as e:
        print(f"[ERROR] Connection Failed: {e}")
        print("Tip: Ensure 'iperf -s -p 5001' is running on host h1 (without UDP -u option).")
        return

    print("[ABR] Connection Established. Starting Adaptive Streaming...")
    print("------------------------------------------------------------------")
    print(f"{'Time':<8} | {'Quality':<12} | {'Bitrate':<10} | {'TxTime':<8} | {'Status'}")
    print("------------------------------------------------------------------")

    start_time = time.time()
    current_idx = 3  # Start by attempting the highest quality (1080p)

    # Simulation loop for the specified duration
    while (time.time() - start_time) < duration_sec:
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
            print("[ERROR] Connection closed by remote host.")
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
        print(f"{elapsed_total}s      | {quality_name:<12} | {bitrate/1e6:.1f}M      | {tx_time:.2f}s    | {status}")

        # 4. Pacing: if we sent too fast, sleep the remainder to mimic real-time playback
        # Example: if 1 second of video sent in 0.5 sec, rest for 0.5 sec (TCP flow control)
        sleep_time = max(0, 1.0 - tx_time)
        time.sleep(sleep_time)

    print("\n[ABR] Streaming Finished.")
    sock.close()


if __name__ == "__main__":
    try:
        dur = input("Enter playback duration (seconds) (default 60): ")
        dur = int(dur) if dur.strip() else 60
        run_abr_simulation(dur)
    except KeyboardInterrupt:
        print("\n[STOP] User interrupted.")
