#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink  # TC(Traffic Control) based link
from mininet.cli import CLI      # CLI(Command Line Interface)
from mininet.log import setLogLevel, info


def video_download_topology():
    """
    Topology overview:
      - h1: Video user (Video User)
      - h2: File download user (Download User)
      - vSrv: Video server (Video Server)
      - dSrv: Download server (Download Server)
      - s1: Access switch near users (Access Switch)
      - s2: Switch near servers (Server Switch)
      - The link between s1 and s2 is a 10 Mbps bottleneck

      All hosts use the same subnet (10.0.0.0/24) and the switches operate at L2,
      so routing is unnecessary for communication.
    """

    net = Mininet(
        controller=RemoteController,
        switch=OVSKernelSwitch,
        link=TCLink,          # QoS knobs such as bw, delay, and loss
        autoSetMacs=True,
        autoStaticArp=True
    )

    info('*** Adding controller\n')
    # Assume the Ryu Controller runs on localhost (port 6633)
    # If using a different port (e.g., 6653), adjust here or in ryu-manager.
    c0 = net.addController(
        'c0',
        controller=RemoteController,
        ip='127.0.0.1',
        port=6633
    )

    info('*** Adding hosts (users)\n')
    # Same LAN: 10.0.0.0/24
    h1 = net.addHost('h1', ip='10.0.0.1/24')   # Video client
    h2 = net.addHost('h2', ip='10.0.0.2/24')   # Download client

    info('*** Adding servers\n')
    vSrv = net.addHost('vSrv', ip='10.0.0.10/24')  # Video server
    dSrv = net.addHost('dSrv', ip='10.0.0.20/24')  # Download server

    info('*** Adding switches\n')
    s1 = net.addSwitch('s1')  # User-side switch
    s2 = net.addSwitch('s2')  # Server-side switch

    info('*** Creating links\n')
    # User ↔ s1: > 100 Mbps (ample bandwidth)
    net.addLink(h1, s1)
    net.addLink(h2, s1)

    # s1 ↔ s2: 10 Mbps bottleneck (congestion point)
    net.addLink(s1, s2, bw=10)

    # Servers ↔ s2: > 100 Mbps (plenty of bandwidth on server side)
    net.addLink(vSrv, s2)
    net.addLink(dSrv, s2)

    info('*** Starting network\n')
    net.start()

    # --- Automatically start iperf receivers ---
    # Prepare h1 and h2 to receive data.
    info('*** Starting iperf servers (Receivers) on h1 (video user) and h2 (download user)\n')

    # h1 (Video User): TCP ABR listener (Port 5001)
    # nc: netcat command
    #  -l: Listen (server mode)
    #  -k: Keep-alive (stay running after disconnect)
    #  -p 5001: use port 5001
    #  > /dev/null: discard received data
    h1.cmd('nohup nc -lk -p 5001 > /dev/null 2>&1 &')
    # h1.cmd('nohup iperf -s -p 5001 > /tmp/iperf_h1.log 2>&1 &')

    # h2 (Download User): TCP listener (Port 5002)
    # -s: Server mode (receive)
    # -p 5002: Port 5002
    h2.cmd('nohup iperf -s -p 5002 > /tmp/iperf_h2.log 2>&1 &')

    info('*** iperf receivers started:\n')
    info('    - h1 (Video User):   Listening TCP ABR on port 5001\n')
    info('    - h2 (Download User): Listening TCP on port 5002\n')

    info('*** Network is ready. Use "xterm vSrv dSrv" to generate traffic.\n')
    CLI(net)   # Enter Mininet CLI

    info('*** Stopping network\n')
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    video_download_topology()
