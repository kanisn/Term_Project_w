# qos_ryu_app.py
# Mininet 토폴로지 (s1=UserSide, s2=ServerSide) 구조를 반영한 모니터링 및 QoS 컨트롤러

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.ofproto import ofproto_v1_3
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from ryu.lib.packet import packet, ethernet, ipv4, tcp, udp
from webob import Response
import json, time

# YANG 모델 파서 (기존 파일 사용)
from yang_parser import get_required_policy_keys

REST_URL = '/qos-policies'
STATS_URL = '/stats'

# 포트 매핑 (Mininet: vSrv->5001, dSrv->5002)
POLICY_PORT_MAP = {
    "video": 5001,
    "download": 5002
}

def mbps_to_kbps(mbps):
    return int(mbps * 1000)

class QoSController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = { 'wsgi': WSGIApplication }

    def __init__(self, *args, **kwargs):
        super(QoSController, self).__init__(*args, **kwargs)
        self.datapaths = {} 
        self.REQUIRED_POLICY_KEYS = get_required_policy_keys()
        
        # REST API 등록
        wsgi = kwargs['wsgi']
        wsgi.register(RestQoSController, { 'qos_app': self })

        # 모니터링 스레드 시작
        self.monitor_thread = hub.spawn(self._monitor)
        
        # 통계 데이터 저장소
        # raw_stats: { dpid: { port_num: { byte_count: 0, packet_count: 0, time: 0.0 } } }
        self.prev_stats = {}
        
        # 가공된 네트워크 상태 (Client가 조회할 데이터)
        self.net_status = {
            "video_bps": 0,      # S1(User)에 도착한 비디오 속도
            "download_bps": 0,   # S1(User)에 도착한 다운로드 속도
            "video_loss": 0,     # S2(TX) - S1(RX) 패킷 차이
            "download_loss": 0,
            "total_bps": 0
        }

    # --- 기본 Flow 설치 ---
    def install_base_flows(self, dp):
        ofp = dp.ofproto
        parser = dp.ofproto_parser

        # 1. ARP, ICMP, 기본 통신 허용 (Priority 10)
        match_arp = parser.OFPMatch(eth_type=0x0806)
        match_icmp = parser.OFPMatch(eth_type=0x0800, ip_proto=1)
        actions = [parser.OFPActionOutput(ofp.OFPP_NORMAL)]
        
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=10, match=match_arp, instructions=[parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]))
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=10, match=match_icmp, instructions=[parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]))
        
        # 2. Default: Normal Forwarding (Priority 0)
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=0, match=parser.OFPMatch(), instructions=[parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]))
        
        self.logger.info(f"Initialized Switch: {dp.id}")

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
            self.install_base_flows(dp)
        elif ev.state == DEAD_DISPATCHER:
            if dp.id in self.datapaths:
                del self.datapaths[dp.id]

    # --- 모니터링 ---
    def _monitor(self):
        while True:
            for dp in self.datapaths.values():
                self._request_stats(dp)
            hub.sleep(1) # 1초 주기

    def _request_stats(self, datapath):
        parser = datapath.ofproto_parser
        req = parser.OFPFlowStatsRequest(datapath)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        body = ev.msg.body
        
        # 현재 스위치에서의 트래픽 카운트
        vid_pkts = 0
        vid_bytes = 0
        dl_pkts = 0
        dl_bytes = 0

        for stat in body:
            # Video (UDP 5001)
            if stat.match.get('ip_proto') == 17 and stat.match.get('udp_dst') == 5001:
                vid_pkts += stat.packet_count
                vid_bytes += stat.byte_count
            # Download (TCP 5002)
            elif stat.match.get('ip_proto') == 6 and stat.match.get('tcp_dst') == 5002:
                dl_pkts += stat.packet_count
                dl_bytes += stat.byte_count

        # 이전 값과 비교하여 속도(bps) 계산을 위한 임시 저장
        # 여기서는 단순화를 위해 전역 상태(self.net_status) 업데이트 로직을 별도로 수행
        # S1 (User Side, dpid=1): 수신량(Throughput) 측정 기준
        # S2 (Server Side, dpid=2): 송신량(Source) 측정 기준 -> Loss 계산용
        
        current_time = time.time()
        
        # DPID별 데이터 저장
        if dpid not in self.prev_stats:
            self.prev_stats[dpid] = {}

        last_vid_bytes = self.prev_stats[dpid].get('vid_bytes', 0)
        last_dl_bytes = self.prev_stats[dpid].get('dl_bytes', 0)
        last_time = self.prev_stats[dpid].get('time', current_time - 1)
        
        time_diff = max(0.001, current_time - last_time)
        
        # 속도 계산 (Bits per second)
        vid_bps = (vid_bytes - last_vid_bytes) * 8 / time_diff
        dl_bps = (dl_bytes - last_dl_bytes) * 8 / time_diff
        
        # 상태 업데이트
        self.prev_stats[dpid] = {
            'vid_bytes': vid_bytes, 'vid_pkts': vid_pkts,
            'dl_bytes': dl_bytes, 'dl_pkts': dl_pkts,
            'time': current_time,
            'vid_speed': vid_bps, 'dl_speed': dl_bps
        }

        # S1, S2 데이터가 모두 모였을 때 전체 상태 집계
        # dpid 1: s1 (User), dpid 2: s2 (Server) (Mininet 기본 할당)
        s1_stats = self.prev_stats.get(1)
        s2_stats = self.prev_stats.get(2)

        if s1_stats and s2_stats:
            # Throughput은 User가 받는 양(S1 기준)
            self.net_status['video_bps'] = s1_stats['vid_speed']
            self.net_status['download_bps'] = s1_stats['dl_speed']
            self.net_status['total_bps'] = s1_stats['vid_speed'] + s1_stats['dl_speed']

            # Loss는 Server가 보낸 양(S2) - User가 받은 양(S1)
            # 패킷 카운트 누적값의 차이를 이용 (단, 타이밍 이슈로 음수가 나올 수 있어 0 처리)
            # 정확도를 위해 윈도우(구간) 차이가 아닌 누적 차이를 보거나, 구간 속도 차이를 봅니다.
            # 여기서는 '구간 속도 차이'를 사용하여 순간 Loss Rate를 추정합니다.
            
            # 예상되는 TX(S2) - 실제 RX(S1)
            vid_loss_rate = max(0, s2_stats['vid_speed'] - s1_stats['vid_speed'])
            dl_loss_rate = max(0, s2_stats['dl_speed'] - s1_stats['dl_speed'])

            # 패킷 단위 차이 (디버깅용)
            pkt_loss = max(0, s2_stats['vid_pkts'] - s1_stats['vid_pkts'])
            
            # Loss 비율로 환산하거나, 손실된 대역폭 자체를 기록
            self.net_status['video_loss'] = vid_loss_rate # bps 단위 손실
            self.net_status['download_loss'] = dl_loss_rate

    # --- QoS 정책 적용 ---
    def apply_policies(self, policies_list):
        policies = { p['name']: p for p in policies_list }

        # 정책 적용: 병목 제어를 위해 S2(Server Side, dpid=2)에 Meter 설치가 중요함
        # 안전을 위해 모든 스위치에 설치
        for dp in self.datapaths.values():
            ofp = dp.ofproto
            parser = dp.ofproto_parser

            for name, pol in policies.items():
                meter_id = max(1, int(pol.get('priority', 1)))
                bw_mbps = int(pol.get('bandwidth-limit', 1))
                kbps = mbps_to_kbps(bw_mbps)
                
                # Meter Mod
                bands = [parser.OFPMeterBandDrop(rate=kbps, burst_size=max(1000, int(kbps/10)))]
                req = parser.OFPMeterMod(datapath=dp, command=ofp.OFPMC_ADD, flags=ofp.OFPMF_KBPS, meter_id=meter_id, bands=bands)
                try:
                    dp.send_msg(req)
                except: # 이미 존재하면 Modify
                    req = parser.OFPMeterMod(datapath=dp, command=ofp.OFPMC_MODIFY, flags=ofp.OFPMF_KBPS, meter_id=meter_id, bands=bands)
                    dp.send_msg(req)

                # Flow Mod
                dst_port = POLICY_PORT_MAP.get(name)
                if not dst_port: continue
                
                # Protocol 구분
                if name == "video": # UDP
                    match = parser.OFPMatch(eth_type=0x0800, ip_proto=17, udp_dst=dst_port)
                else: # TCP (download, background)
                    match = parser.OFPMatch(eth_type=0x0800, ip_proto=6, tcp_dst=dst_port)

                # Meter -> Normal Forwarding
                inst = [
                    parser.OFPInstructionMeter(meter_id),
                    parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, [parser.OFPActionOutput(ofp.OFPP_NORMAL)])
                ]
                
                # Priority: 정책값 + 100
                prio = 100 + int(pol.get('priority', 1))
                
                dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=prio, match=match, instructions=inst))

        self.logger.info(f"Applied Policies: {list(policies.keys())}")

class RestQoSController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(RestQoSController, self).__init__(req, link, data, **config)
        self.qos_app = data['qos_app']

    @route('qos', REST_URL, methods=['PUT', 'POST'])
    def put_policies(self, req, **kwargs):
        try:
            data = json.loads(req.body.decode('utf-8'))
            policies = data.get('qos-policies:qos-policies', {}).get('policy', [])
            self.qos_app.apply_policies(policies)
            return Response(status=200, body=json.dumps({"msg": "OK"}), content_type='application/json')
        except Exception as e:
            return Response(status=500, body=str(e))

    @route('qos_stats', STATS_URL, methods=['GET'])
    def get_stats(self, req, **kwargs):
        # 현재 네트워크 상태 반환
        return Response(content_type='application/json', body=json.dumps(self.qos_app.net_status))