# REST API 로 QoS 정책을 받고, YANG 모델로 필드를 검증한 뒤 OpenFlow 1.3 스위치에
# Meter/Flow 를 설치하는 Ryu 애플리케이션.

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

# YANG 모델을 로드하기 위한 로컬 파서
from yang_parser import get_required_policy_keys

# QoS 정책을 수신하는 REST 경로
REST_URL = '/qos-policies'

# 정책 이름을 미니넷 데모에서 사용할 TCP 목적지 포트로 매핑한다.
POLICY_PORT_MAP = {
    "video": 5001,
    "download": 5002,
    "background": 5003
}

# Mbps 단위 입력을 OpenFlow meter 가 사용하는 kbps 단위로 변환한다.
def mbps_to_kbps(mbps):
    return int(mbps * 1000)

# QoSController: 스위치 연결 상태를 관리하고 REST 요청을 받아 Meter/Flow 를 구성한다.
class QoSController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = { 'wsgi': WSGIApplication }

    def __init__(self, *args, **kwargs):
        super(QoSController, self).__init__(*args, **kwargs)
        self.datapaths = {}  # 연결된 스위치의 dpid -> datapath 매핑
        self.REQUIRED_POLICY_KEYS = get_required_policy_keys()  # YANG 모델에서 필수 필드를 불러온다.
        # YANG에서 정의한 필수 필드를 미리 로드하여 REST 요청 검증에 재사용한다.
        self.logger.info("Loaded REQUIRED_POLICY_KEYS from YANG: %s", self.REQUIRED_POLICY_KEYS)
        # REST 컨트롤러 등록
        wsgi = kwargs['wsgi']
        wsgi.register(RestQoSController, { 'qos_app': self })

    def install_base_flows(self, dp):
        # 스위치가 처음 연결될 때 ARP/ICMP 허용 및 NORMAL 포워딩을 설정하는 기본 플로우를 설치한다.
        ofp = dp.ofproto
        parser = dp.ofproto_parser

        # 1. ARP 허용
        match_arp = parser.OFPMatch(eth_type=0x0806)
        actions = [parser.OFPActionOutput(ofp.OFPP_NORMAL)]
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=dp, priority=10, match=match_arp, instructions=inst)
        dp.send_msg(mod)

        # 2. ICMP 허용 (ping 테스트용)
        match_icmp = parser.OFPMatch(eth_type=0x0800, ip_proto=1)
        mod = parser.OFPFlowMod(datapath=dp, priority=10, match=match_icmp, instructions=inst)
        dp.send_msg(mod)

        # 3. 기본 NORMAL 포워딩 (폴백 경로)
        match_all = parser.OFPMatch()
        mod = parser.OFPFlowMod(datapath=dp,
                                priority=0,
                                match=match_all,
                                instructions=inst)
        dp.send_msg(mod)

        self.logger.info("Installed base flows (ARP/ICMP/NORMAL) on dp %s", dp.id)

    # 스위치 연결 상태 처리
    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, CONFIG_DISPATCHER, HANDSHAKE_DISPATCHER])
    def _state_change_handler(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
            self.install_base_flows(dp)
            # 스위치가 등록되면 바로 기본 플로우를 내려서 네트워크가 즉시 동작하도록 한다.
            self.logger.info("Datapath connected: %s", dp.id)
        elif ev.state == ofp_event.EventOFPStateChange.__dict__.get('DISCONNECTED', None):
            if dp.id in self.datapaths:
                del self.datapaths[dp.id]
                self.logger.info("Datapath disconnected: %s", dp.id)

    # REST 로 받은 정책 목록을 해석해 각 스위치에 Meter 와 Flow 를 설정한다.
    def apply_policies(self, policies_list):
        if not isinstance(policies_list, list):
            raise ValueError("policies_list must be a list")

        # 리스트를 이름 기준 딕셔너리로 변환해 조회를 단순화
        policies = { p['name']: p for p in policies_list }

        # 연결된 스위치를 순회하며 meter 와 flow 를 설정
        for dp in self.datapaths.values():
            ofp = dp.ofproto
            parser = dp.ofproto_parser

            # 정책의 priority 값을 기반으로 meter_id 를 정해 추가하거나 수정한다.
            for name, pol in policies.items():
                meter_id = max(1, int(pol.get('priority', 1)))
                bw_mbps = int(pol.get('bandwidth-limit', 1))
                kbps = mbps_to_kbps(bw_mbps)

                bands = [parser.OFPMeterBandDrop(rate=kbps, burst_size=max(1000, int(kbps/10)))]
                flags = ofp.OFPMF_KBPS

                try:
                    req = parser.OFPMeterMod(datapath=dp,
                                             command=ofp.OFPMC_ADD,
                                             flags=flags,
                                             meter_id=meter_id,
                                             bands=bands)
                    dp.send_msg(req)
                    self.logger.info("Sent OFPMC_ADD meter %d on dp %s (rate=%dkbps) for policy %s", meter_id, dp.id, kbps, name)
                except Exception as e:
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

                # 정책 이름에 대응하는 TCP 목적지 포트로 매칭 조건을 만든다.
                tcp_port = POLICY_PORT_MAP.get(name)
                if tcp_port is None:
                    self.logger.warning("No port mapping for policy '%s' -> skipping flow install", name)
                    continue

                match = parser.OFPMatch(eth_type=0x0800, ip_proto=6, tcp_dst=tcp_port)
                # 동작: 지정한 meter 를 적용한 뒤 NORMAL 포워딩을 수행한다.
                inst = [
                    parser.OFPInstructionMeter(meter_id),
                    parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS,
                                                 [parser.OFPActionOutput(ofp.OFPP_NORMAL)])
                ]
                # 우선순위는 정책 priority 값에 100을 더해 구분한다.
                priority = 100 + int(pol.get('priority', 1))
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


# REST API 를 통해 QoS 정책을 갱신하는 컨트롤러
class RestQoSController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(RestQoSController, self).__init__(req, link, data, **config)
        self.qos_app = data['qos_app']

    # PUT 메서드: 정책 리스트를 JSON 으로 수신
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
        # 정책 목록 추출
        if 'qos-policies:qos-policies' in data:
            policies_list = data['qos-policies:qos-policies'].get('policy', [])
        else:
            return Response(
                status=400,
                content_type='application/json',
                body=json.dumps({"error": "Invalid payload structure"})
            )
        # YANG 에 정의된 필수 필드가 누락되었는지 확인
        missing_keys = self.qos_app.REQUIRED_POLICY_KEYS - set(policies_list[0].keys())
        if missing_keys:
            return Response(
                status=400,
                content_type='application/json',
                body=json.dumps({"error": "Missing required fields",
                                 "missing": list(missing_keys)})
            )

        try:
            self.qos_app.apply_policies(policies_list)
        except Exception as e:
            return Response(
                status=500,
                content_type='application/json',
                body=json.dumps({"error": "Failed to apply policies", "detail": str(e)})
            )

        return Response(status=204)




    # PATCH 메서드는 PUT 과 동일하게 동작
    @route('qos', REST_URL, methods=['PATCH'])
    def patch_policies(self, req, **kwargs):
        return self.put_policies(req, **kwargs)
