import socket
import time
import math

# --- Configuration ---
TARGET_IP = "10.0.0.1"
TARGET_PORT = 5001


def send_video_like_udp(mbps, duration, fps=30, pkt_size=1200):
    """
    Simulate frame-based UDP video traffic.
    Ensures the stream completes cleanly within the given duration (seconds).
    """
    duration = int(duration)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    payload = b'x' * pkt_size

    bytes_per_sec = mbps * 1_000_000 / 8
    bytes_per_frame = bytes_per_sec / fps
    pkts_per_frame = math.ceil(bytes_per_frame / pkt_size)

    total_frames = int(duration * fps)

    print(f"\n[VIDEO] Start Streaming: {mbps} Mbps for {duration} sec")
    print(f"[VIDEO] FPS={fps}, Packet={pkt_size}B, Packets/Frame={pkts_per_frame}")
    print("------------------------------------------------------------")

    for frame in range(total_frames):
        frame_start = time.time()

        for _ in range(pkts_per_frame):
            sock.sendto(payload, (TARGET_IP, TARGET_PORT))

        elapsed = time.time() - frame_start
        sleep_time = max(0, (1.0 / fps) - elapsed)
        time.sleep(sleep_time)

    print("[VIDEO] Streaming Finished.\n")
    sock.close()


def print_menu():
    print("\n===========================================")
    print("      VIDEO Streaming Generator (vSrv)     ")
    print("===========================================")
    print(f"Target: {TARGET_IP}:{TARGET_PORT} (UDP)")
    print("Sends UDP traffic that mimics real video streaming.")
    print("-------------------------------------------")
    print("1. 360p  (SD)   - 1 Mbps")
    print("2. 720p  (HD)   - 3 Mbps")
    print("3. 1080p (FHD)  - 6 Mbps")
    print("4. 4K    (UHD)  - 15 Mbps")
    print("0. Exit")
    print("===========================================")


def run_simulation():
    while True:
        print_menu()
        choice = input("Select quality >> ")

        if choice == '0':
            print("Exiting program.")
            break

        if choice == '1':
            mbps = 1
            quality_name = "360p (SD)"
        elif choice == '2':
            mbps = 3
            quality_name = "720p (HD)"
        elif choice == '3':
            mbps = 6
            quality_name = "1080p (FHD)"
        elif choice == '4':
            mbps = 15
            quality_name = "4K (UHD)"
        else:
            print("Invalid input.")
            continue

        duration = input("Enter playback duration (seconds) (default 30): ")
        if duration.strip() == "":
            duration = 30  # Default

        print(f"\n[INFO] Starting '{quality_name}' stream ({mbps} Mbps, {duration} sec)")
        print(f"[INFO] Receiver: h1 should run 'iperf -s -u -p 5001'")

        try:
            send_video_like_udp(mbps, int(duration))
        except Exception as e:
            print(f"[ERROR] {e}\n")

        # Return to menu after streaming completes
        print("[INFO] Streaming finished → returning to menu.\n")


if __name__ == "__main__":
    run_simulation()
