#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time

# --- 설정 ---
# Mininet 토폴로지상 h2(Download User)의 IP
TARGET_IP = "10.0.0.2"
TARGET_PORT = 5002

def print_menu():
    print("\n===========================================")
    print("    DOWNLOAD Traffic Generator (dSrv)      ")
    print("===========================================")
    print(f"Target: {TARGET_IP}:{TARGET_PORT} (TCP)")
    print("대용량 파일 다운로드를 모사합니다.")
    print("-------------------------------------------")
    print("1. 일반 다운로드 (최대 속도, 지정 시간만 전송)")
    print("2. 지속 다운로드 (최대 속도, 끌 때까지 계속)")
    print("0. 종료")
    print("===========================================")

def run_simulation():
    while True:
        print_menu()
        choice = input("메뉴 선택 >> ").strip()

        if choice == '0':
            print("프로그램을 종료합니다.")
            break

        # --- 1번: 지정 시간만 다운로드 ---
        if choice == '1':
            try:
                dur = input("전송 시간(초) 입력 (기본 30): ").strip()
                if dur == "":
                    dur = "30"
                if not dur.isdigit():
                    print("숫자를 입력해주세요.")
                    continue
                # [수정] -P 10 옵션 추가: 10개의 병렬 연결로 대역폭 강제 점유
                print(f"\n[INFO] 공격적 다운로드 시작... ({dur}초, 병렬 연결 10개)")
                # -c: Client, -p: Port, -t: Time, -i: Interval -P: Parallel
                os.system(f"iperf -c {TARGET_IP} -p {TARGET_PORT} -t {dur} -P 10 -i 1") # 여기를 수정

                #print(f"\n[INFO] 일반 다운로드 시작... ({dur}초, TCP 최대 속도)")
                #os.system(f"iperf -c {TARGET_IP} -p {TARGET_PORT} -t {dur} -i 1")

            except KeyboardInterrupt:
                print("\n\n[STOP] 다운로드를 중지합니다.")
            except Exception as e:
                print(f"[ERROR] {e}")

        # --- 2번: 계속 다운로드 (ON 상태 유지) ---
        elif choice == '2':
            try:
                print(f"\n[INFO] 지속 다운로드 시작 (TCP 최대 속도)")
                print("[INFO] Ctrl+C를 누르면 중지 후 메뉴로 돌아갑니다.")

                # 사용자가 강제로 끌 때까지 무한 반복
                while True:
                    # [수정] -P 10 옵션 추가
                    cmd = f"iperf -c {TARGET_IP} -p {TARGET_PORT} -t 5 -P 10 -i 1" # 여기를 수정
                    os.system(cmd)
                    
                    # -t 5: 5초 동안 테스트, 끝나면 다시 바로 시작 (끊김 없는 부하 유지)
                    #cmd = f"iperf -c {TARGET_IP} -p {TARGET_PORT} -t 5 -i 1"
                    #os.system(cmd)

            except KeyboardInterrupt:
                print("\n\n[STOP] 지속 다운로드를 중지합니다.")
            except Exception as e:
                print(f"[ERROR] {e}")

        else:
            print("잘못된 선택입니다.")

        input("엔터를 누르면 메뉴로 돌아갑니다...")

if __name__ == "__main__":
    run_simulation()