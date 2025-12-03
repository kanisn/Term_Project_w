import socket
import time
import math
import os

# --- 설정 ---
TARGET_IP = "10.0.0.1"
TARGET_PORT = 5001


def send_video_like_udp(mbps, duration, fps=30, pkt_size=1200):
    """
    실비디오 패턴 (Frame-Based Traffic)
    duration(초) 동안 정상적으로 끝나도록 수정
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
    print("실제 비디오 스트리밍과 유사한 UDP 트래픽을 전송합니다.")
    print("-------------------------------------------")
    print("1. 360p  (SD)   - 1 Mbps")
    print("2. 720p  (HD)   - 3 Mbps")
    print("3. 1080p (FHD)  - 6 Mbps")
    print("4. 4K    (UHD)  - 15 Mbps")
    print("0. 종료")
    print("===========================================")


def run_simulation():
    while True:
        print_menu()
        choice = input("화질을 선택하세요 >> ")

        if choice == '0':
            print("프로그램을 종료합니다.")
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
            print("잘못된 입력입니다.")
            continue

        duration = input("재생 시간(초)을 입력하세요 (기본 30초): ")
        if duration.strip() == "":
            duration = 30  # 기본값

        print(f"\n[INFO] '{quality_name}' 스트리밍 시작 ({mbps} Mbps, {duration}초)")
        print(f"[INFO] Receiver: h1에서 'iperf -s -u -p 5001' 필요")

        try:
            send_video_like_udp(mbps, int(duration))
        except Exception as e:
            print(f"[ERROR] {e}\n")

        # 스트리밍이 끝났으면 다시 메뉴로 돌아감
        print("[INFO] 스트리밍 완료 → 메뉴로 돌아갑니다.\n")


if __name__ == "__main__":
    run_simulation()
