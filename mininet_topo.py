# mininet_topo.py
#
# Simple Mininet topology:
#  - 1 switch, 3 hosts (h1, h2, h3)
#  - Each host will run an iperf server on different ports for demo:
#       h1: video -> port 5001
#       h2: download -> port 5002
#       h3: background -> port 5003
#
# Usage: sudo python3 mininet_topo.py
#
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
    # Connect to remote Ryu controller (default at 127.0.0.1:6653)
    c0 = RemoteController('c0', ip='127.0.0.1', port=6653) # 创建一个名为 c0 的控制器对象。它连接到 本地主机 (127.0.0.1) 上的 6653 端口，这是 Ryu 等 SDN 控制器的默认 OpenFlow 端口。
    # 实例化 Mininet。topo：使用定义的拓扑；controller：使用远程控制器 c0；switch：使用 OVSSwitch（Open vSwitch）
    net = Mininet(topo=topo, controller=c0, switch=OVSSwitch)
    # 执行所有配置，创建虚拟主机、交换机和链接，并让交换机连接到控制器。
    net.start()
    # 打印网络启动成功的消息。
    info("Network started. Launching iperf servers on ports 5001/5002/5003...\n")

    # 从 Mininet 实例中获取 h1, h2, h3 的 Python 对象，以便在其上执行命令。
    h1, h2, h3 = net.get('h1', 'h2', 'h3')


    # 在主机 h1 上启动 iperf 服务器 (-s)，监听 5001 端口 (用于 video 流量分类)。nohup ... & 使其在后台持续运行，并将输出重定向到 /tmp/iperf_h1.log。
    h1.cmd('nohup iperf -s -p 5001 > /tmp/iperf_h1.log 2>&1 &')
    # 在主机 h2 上启动 iperf 服务器，监听 5002 端口 (用于 download 流量分类)。
    h2.cmd('nohup iperf -s -p 5002 > /tmp/iperf_h2.log 2>&1 &')
    # 在主机 h3 上启动 iperf 服务器，监听 5003 端口 (用于 background 流量分类)。
    h3.cmd('nohup iperf -s -p 5003 > /tmp/iperf_h3.log 2>&1 &')

    info("Iperf servers started. You can run clients from other hosts, e.g. from h4 (or from h1->h2):\n")
    info("Example: h1 iperf -c 10.0.0.2 -p 5002 -t 10\n")

    # 暂停脚本执行，进入 Mininet 交互式命令行界面，用户可以在这里运行流量客户端 (如 h1 iperf -c 10.0.0.2 -p 5002 -t 10)。
    CLI(net)
    net.stop()# 当用户退出 CLI 后，关闭并清理所有虚拟网络组件。

if __name__ == '__main__':
    start_network()
