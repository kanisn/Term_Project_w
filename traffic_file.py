#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os

# --- Configuration ---
# IP of h2 (Download User) in the Mininet topology
TARGET_IP = "10.0.0.2"
TARGET_PORT = 5002


def print_menu():
    print("\n===========================================")
    print("    DOWNLOAD Traffic Generator (dSrv)      ")
    print("===========================================")
    print(f"Target: {TARGET_IP}:{TARGET_PORT} (TCP)")
    print("Simulates large file downloads.")
    print("-------------------------------------------")
    print("1. Burst download (max speed, run for set time)")
    print("2. Continuous download (max speed until stopped)")
    print("0. Exit")
    print("===========================================")


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
                    print("Please enter a number.")
                    continue
                # Use -P 10 to create 10 parallel connections for aggressive bandwidth usage
                print(f"\n[INFO] Starting aggressive download... ({dur} sec, 10 parallel connections)")
                # -c: Client, -p: Port, -t: Time, -i: Interval, -P: Parallel streams
                os.system(f"iperf -c {TARGET_IP} -p {TARGET_PORT} -t {dur} -P 10 -i 1")

            except KeyboardInterrupt:
                print("\n\n[STOP] Stopping download.")
            except Exception as e:
                print(f"[ERROR] {e}")

        # --- Option 2: Continuous download ---
        elif choice == '2':
            try:
                print(f"\n[INFO] Starting continuous download (max TCP speed)")
                print("[INFO] Press Ctrl+C to stop and return to the menu.")

                # Loop until user stops manually
                while True:
                    # Use -P 10 for multiple parallel flows
                    cmd = f"iperf -c {TARGET_IP} -p {TARGET_PORT} -t 5 -P 10 -i 1"
                    os.system(cmd)

            except KeyboardInterrupt:
                print("\n\n[STOP] Continuous download stopped.")
            except Exception as e:
                print(f"[ERROR] {e}")

        else:
            print("Invalid selection.")

        input("Press Enter to return to the menu...")


if __name__ == "__main__":
    run_simulation()
