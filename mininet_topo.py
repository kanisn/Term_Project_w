# mininet_topo.py
# 단일 스위치와 3개 호스트로 구성된 간단한 토폴로지.
# 각 호스트는 iperf 서버를 실행하며 포트 5001/5002/5003 으로 트래픽을 구분한다.
# 사용법: sudo python3 mininet_topo.py
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info
import time

class SimpleQoSTopo(Topo):
    def build(self):
        s1 = self.addSwitch('s1')
        h1 = self.addHost('h1', ip='10.0.0.1')
        h2 = self.addHost('h2', ip='10.0.0.2')
        h3 = self.addHost('h3', ip='10.0.0.3')
        self.addLink(h1, s1)
        self.addLink(h2, s1)
        self.addLink(h3, s1)

def start_network():
    setLogLevel('info')
    topo = SimpleQoSTopo()
    # 원격 Ryu 컨트롤러(127.0.0.1:6653)에 연결
    c0 = RemoteController('c0', ip='127.0.0.1', port=6653)
    net = Mininet(topo=topo, controller=c0, switch=OVSSwitch)
    net.start()
    info("Network started. Launching iperf servers on ports 5001/5002/5003...\n")

    # 호스트 객체 획득
    h1, h2, h3 = net.get('h1', 'h2', 'h3')

    # 각 호스트에서 iperf 서버를 백그라운드로 실행
    h1.cmd('nohup iperf -s -p 5001 > /tmp/iperf_h1.log 2>&1 &')
    h2.cmd('nohup iperf -s -p 5002 > /tmp/iperf_h2.log 2>&1 &')
    h3.cmd('nohup iperf -s -p 5003 > /tmp/iperf_h3.log 2>&1 &')

    info("Iperf servers started. 예: h1 iperf -c 10.0.0.2 -p 5002 -t 10\n")

    # Mininet CLI 로 진입하여 트래픽을 직접 생성할 수 있다.
    CLI(net)
    net.stop()

if __name__ == '__main__':
    start_network()
