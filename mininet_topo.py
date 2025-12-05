#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink  # TC(Traffic Control) 기반 링크
from mininet.cli import CLI      # CLI(Command Line Interface)
from mininet.log import setLogLevel, info

def video_download_topology():
    """
    구조 요약:
      - h1: 비디오 시청 사용자 (Video User)
      - h2: 파일 다운로드 사용자 (Download User)
      - vSrv: 비디오 서버 (Video Server)
      - dSrv: 다운로드 서버 (Download Server)
      - s1: 사용자 쪽 액세스 스위치 (Access Switch)
      - s2: 서버 쪽 스위치 (Server Switch)
      - s1 ↔ s2 구간을 10Mbps 병목 링크(Bottleneck Link)로 설정

      모든 호스트는 같은 IP 대역(10.0.0.0/24)을 사용하고,
      스위치는 L2 스위치이므로 라우터 없이도 통신 가능.
    """

    net = Mininet(
        controller=RemoteController,
        switch=OVSKernelSwitch,
        link=TCLink,          # bw, delay, loss 같은 QoS 설정 가능
        autoSetMacs=True,
        autoStaticArp=True
    )

    info('*** Adding controller\n')
    # Ryu Controller를 localhost에서 실행한다고 가정 (포트 6633)
    # 기존 코드나 Ryu 실행 시 포트가 6653인 경우, ryu-manager 실행 옵션이나 이곳을 맞춰야 합니다.
    c0 = net.addController(
        'c0',
        controller=RemoteController,
        ip='127.0.0.1',
        port=6633
    )

    info('*** Adding hosts (users)\n')
    # 같은 LAN 대역: 10.0.0.0/24
    h1 = net.addHost('h1', ip='10.0.0.1/24')   # 비디오 클라이언트
    h2 = net.addHost('h2', ip='10.0.0.2/24')   # 다운로드 클라이언트

    info('*** Adding servers\n')
    vSrv = net.addHost('vSrv', ip='10.0.0.10/24')  # 비디오 서버
    dSrv = net.addHost('dSrv', ip='10.0.0.20/24')  # 다운로드 서버

    info('*** Adding switches\n')
    s1 = net.addSwitch('s1')  # 사용자 쪽 스위치
    s2 = net.addSwitch('s2')  # 서버 쪽 스위치

    info('*** Creating links\n')
    # 사용자 ↔ s1: > 100Mbps (충분히 넓은 링크)
    net.addLink(h1, s1)
    net.addLink(h2, s1)

    # s1 ↔ s2: 10Mbps 병목 링크 (여기가 혼잡 구간)
    net.addLink(s1, s2, bw=10)

    # 서버 ↔ s2: > 100Mbps (서버 쪽은 넉넉한 대역폭)
    net.addLink(vSrv, s2)
    net.addLink(dSrv, s2)

    info('*** Starting network\n')
    net.start()

    # --- iperf 서버(수신자) 자동 실행 ---
    # h1, h2가 데이터를 받을 준비를 합니다.
    info('*** Starting iperf servers (Receivers) on h1 (video user) and h2 (download user)\n')
    
    # h1 (Video User): TCP ABR 수신 대기 (Port 5001)
    # nc: netcat 명령어
    # # -l: Listen (서버 모드)
    # # -k: Keep-alive (연결이 끊겨도 서버를 끄지 않고 계속 대기)
    # # -p 5001: 5001번 포트 사용
    # # > /dev/null: 받는 데이터는 저장하지 않고 바로 삭제
    h1.cmd('nohup nc -lk -p 5001 > /dev/null 2>&1 &')
    #h1.cmd('nohup iperf -s -p 5001 > /tmp/iperf_h1.log 2>&1 &')
    
    # h2 (Download User): TCP 수신 대기 (Port 5002)
    # -s: Server mode (수신)
    # -p 5002: Port 5002
    h2.cmd('nohup iperf -s -p 5002 > /tmp/iperf_h2.log 2>&1 &')

    info('*** iperf receivers started:\n')
    info('    - h1 (Video User):   Listening TCP ABR on port 5001\n')
    info('    - h2 (Download User): Listening TCP on port 5002\n')

    info('*** Network is ready. Use "xterm vSrv dSrv" to generate traffic.\n')
    CLI(net)   # Mininet CLI 진입

    info('*** Stopping network\n')
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    video_download_topology()