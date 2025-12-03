# qos_ryu_app.py
# Ryu 控制器本身是一个 OpenFlow 控制器，负责交换机流表和 Meter，但它本身没有 REST API 功能。
#   Ryu 控制器应用（REST + YANG 验证 + Meter/Flow 下发）

# Ryu app that:
# - 公开 REST 端点 /qos-policies（PUT/PATCH）
# - 从 yang_parser.get_required_policy_keys() 加载 YANG 必需的键
# - 根据 YANG 字段验证传入的策略列表
# - 对连接的 OpenFlow 1.3 交换机进行流量和流量配置

# Requirements:
#  - ryu (pip install ryu)
#  - pyang (for yang_parser to work)
#
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ipv4, tcp
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from ryu.controller.handler import HANDSHAKE_DISPATCHER
from ryu import cfg

from webob import Response

import json, os
from urllib.parse import urlparse

# import your yang_parser to preserve local YANG loading mechanism
from yang_parser import get_required_policy_keys

# REST path
REST_URL = '/qos-policies'  # We'll accept PUT/PATCH json here

# 将策略“名称”映射到演示 TCP 目标端口（用于 Mininet 演示中的流量分类）。
POLICY_PORT_MAP = {
    "video": 5001,
    "download": 5002,
    "background": 5003
}

# 辅助函数，将 Mbps 转换为 kbps，OpenFlow meter 用 kbps
def mbps_to_kbps(mbps):
    return int(mbps * 1000)

# 在 OpenFlow 中，Meter（流量计） 是一种用于速率限制和流量控制的机制，可以对匹配到的流量进行“打分和处理”。
# 它的核心功能：
#     限制速率：例如限制某条流每秒不超过 10 Mbps
#     动作处理：当流量超过设定速率，可以选择丢包（Drop）或者打标记（DSCP/EXP）
#     多策略支持：一个 Meter 可以应用到一个或多个流上
#
# 换句话说，Meter 就像水管上的阀门，你可以控制流经的水（数据流）速率，超出的部分可以丢掉或标记。


# QoSController： 核心 Ryu 应用，管理交换机连接、Meter 和 Flow 下发
class QoSController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION] # 声明支持的 OpenFlow 版本 1.3
    _CONTEXTS = { 'wsgi': WSGIApplication } # 告诉 Ryu 注入 wsgi 对象，用于 REST API

    def __init__(self, *args, **kwargs):
        super(QoSController, self).__init__(*args, **kwargs)
        self.datapaths = {}  # dpid -> datapath 保存已连接交换机的 dpid -> datapath 映射
        self.REQUIRED_POLICY_KEYS = get_required_policy_keys() # 从本地 YANG 模型获取策略所需字段
        self.logger.info("Loaded REQUIRED_POLICY_KEYS from YANG: %s", self.REQUIRED_POLICY_KEYS)
        # register REST controller
        wsgi = kwargs['wsgi'] # 获取 WSGI 实例
        wsgi.register(RestQoSController, { 'qos_app': self }) # 注册 REST 控制器，把当前 Ryu App 实例传给 REST 控制器

    def install_base_flows(self, dp): # 基础流表函数
        ofp = dp.ofproto
        parser = dp.ofproto_parser

        # 1. allow ARP
        match_arp = parser.OFPMatch(eth_type=0x0806)
        actions = [parser.OFPActionOutput(ofp.OFPP_NORMAL)]
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=dp, priority=10, match=match_arp, instructions=inst)
        dp.send_msg(mod)

        # 2. allow ICMP (for ping)
        match_icmp = parser.OFPMatch(eth_type=0x0800, ip_proto=1)
        mod = parser.OFPFlowMod(datapath=dp, priority=10, match=match_icmp, instructions=inst)
        dp.send_msg(mod)

        # 3. default NORMAL forwarding (fallback)
        match_all = parser.OFPMatch()
        mod = parser.OFPFlowMod(datapath=dp,
                                priority=0,
                                match=match_all,
                                instructions=inst)
        dp.send_msg(mod)

        self.logger.info("Installed base flows (ARP/ICMP/NORMAL) on dp %s", dp.id)

    # 交换机连接状态处理
    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, CONFIG_DISPATCHER, HANDSHAKE_DISPATCHER])
    def _state_change_handler(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
            self.install_base_flows(dp) # 让交换机连接后自动安装基础流表
            self.logger.info("Datapath connected: %s", dp.id)
        elif ev.state == ofp_event.EventOFPStateChange.__dict__.get('DISCONNECTED', None):
            # best-effort removal
            if dp.id in self.datapaths: # datapaths 更新，断开时删除
                del self.datapaths[dp.id]
                self.logger.info("Datapath disconnected: %s", dp.id)

    # 策略应用函数 ： 按策略下发 Meter + 流表
    def apply_policies(self, policies_list):
        if not isinstance(policies_list, list): # 检查传入策略是否为列表
            raise ValueError("policies_list must be a list")

        # 将列表转换成字典 name -> policy，方便查找
        policies = { p['name']: p for p in policies_list }

        # 遍历已连接交换机, install meters and flows
        for dp in self.datapaths.values():
            ofp = dp.ofproto
            parser = dp.ofproto_parser

            # 首先：为了方便起见，移除之前创建的现有meters（可选）
            # 注意：某些交换机不允许删除已预留的meters ID；我们尽量减少此操作。
            # 我们将尝试通过 meter_id = priority（或其派生 ID）来添加/更新meters。
            for name, pol in policies.items():
                # choose meter_id (must be 1..(2^32-1)); avoid 0
                meter_id = max(1, int(pol.get('priority', 1))) # 为每个策略选择 meter_id
                bw_mbps = int(pol.get('bandwidth-limit', 1))
                kbps = mbps_to_kbps(bw_mbps)# 获取带宽限制并转换为 kbps

                # create meter band drop。构建 MeterBandDrop（超出限速就丢包）
                bands = [parser.OFPMeterBandDrop(rate=kbps, burst_size=max(1000, int(kbps/10)))]
                flags = ofp.OFPMF_KBPS  # 速率单位 kbps

                # Build meter mod (ADD or MODIFY) - we'll first try ADD; if exists, we modify.
                # To simplify, we send a MODIFY (controller should handle appropriately).
                # 发送 OFPMC_ADD 消息添加 meter
                # 如果失败，会尝试 OFPMC_MODIFY 修改已存在的 meter
                try:
                    req = parser.OFPMeterMod(datapath=dp,
                                             command=ofp.OFPMC_ADD,
                                             flags=flags,
                                             meter_id=meter_id,
                                             bands=bands)
                    dp.send_msg(req)
                    self.logger.info("Sent OFPMC_ADD meter %d on dp %s (rate=%dkbps) for policy %s", meter_id, dp.id, kbps, name)
                except Exception as e:
                    # fallback to modify
                    try:
                        req = parser.OFPMeterMod(datapath=dp,
                                                 command=ofp.OFPMC_MODIFY,
                                                 flags=flags,
                                                 meter_id=meter_id,
                                                 bands=bands)
                        dp.send_msg(req)
                        self.logger.info("Modified meter %d on dp %s (rate=%dkbps) for policy %s", meter_id, dp.id, kbps, name)
                    except Exception as e2:
                        self.logger.error("Failed to add/modify meter %d on dp %s: %s", meter_id, dp.id, e2)

                # install a flow that matches TCP dst port mapped for this policy
                # 为策略对应的 TCP 端口安装流表
                tcp_port = POLICY_PORT_MAP.get(name)
                if tcp_port is None:
                    self.logger.warning("No port mapping for policy '%s' -> skipping flow install", name)
                    continue

                match = parser.OFPMatch(eth_type=0x0800, ip_proto=6, tcp_dst=tcp_port)
                # instructions: apply meter, then normal output
                inst = [
                    parser.OFPInstructionMeter(meter_id),
                    parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS,
                                                 [parser.OFPActionOutput(ofp.OFPP_NORMAL)])
                ]
                # 优先级：policy 优先级越高，优先级越高。
                priority = 100 + int(pol.get('priority', 1)) # 流表优先级 = 100 + 策略 priority
                mod = parser.OFPFlowMod(datapath=dp,
                                        priority=priority,
                                        match=match,
                                        instructions=inst,
                                        table_id=0)
                try:
                    dp.send_msg(mod)
                    self.logger.info("Installed flow on dp %s: match tcp_dst=%d -> meter %d (priority=%d)",
                                     dp.id, tcp_port, meter_id, priority)
                except Exception as e:
                    self.logger.error("Failed to install flow on dp %s: %s", dp.id, e)

        return True


# REST 控制器， REST API 接口，PUT/PATCH 更新策略
class RestQoSController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(RestQoSController, self).__init__(req, link, data, **config)
        self.qos_app = data['qos_app']

    # PUT 方法，接收 JSON payload
    @route('qos', REST_URL, methods=['PUT'])
    def put_policies(self, req, **kwargs):
        try:
            payload = req.body.decode('utf-8')
            data = json.loads(payload)
        except Exception as e:
            return Response(
                status=400,
                content_type='application/json',
                body=json.dumps({"error": "invalid json", "detail": str(e)})
            )
        # 获取策略列表
        if 'qos-policies:qos-policies' in data:
            policies_list = data['qos-policies:qos-policies'].get('policy', [])
        else:
            return Response(
                status=400,
                content_type='application/json',
                body=json.dumps({"error": "Invalid payload structure"})
            )
        # 校验策略是否缺少 YANG 定义的字段
        missing_keys = self.qos_app.REQUIRED_POLICY_KEYS - set(policies_list[0].keys())
        if missing_keys:
            return Response(
                status=400,
                content_type='application/json',
                body=json.dumps({"error": "Missing required fields",
                                 "missing": list(missing_keys)})
            )

        try:
            # 调用 Ryu App 的 apply_policies，真正下发流表和 meter
            self.qos_app.apply_policies(policies_list)
        except Exception as e:
            return Response(
                status=500,
                content_type='application/json',
                body=json.dumps({"error": "Failed to apply policies", "detail": str(e)})
            )

        return Response(status=204)




    # PATCH 方法直接复用 PUT，实现同样功能
    @route('qos', REST_URL, methods=['PATCH'])
    def patch_policies(self, req, **kwargs):
        return self.put_policies(req, **kwargs)
