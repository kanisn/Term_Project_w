# qos_ryu_app.py
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.ofproto import ofproto_v1_3
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from webob import Response
import json
import time

# YANG 모델 파서 임포트
from yang_parser import get_required_policy_keys

# --- 설정 ---
# [수정됨] Decision Engine(Client)의 요청 URL (http://.../qos/qos-policies)과 일치시킴
REST_URL = '/qos/qos-policies'
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
        
        # YANG 파서를 통해 필수 키 목록 로드 (name, priority, bandwidth-limit)
        self.REQUIRED_POLICY_KEYS = get_required_policy_keys()
        self.logger.info(f"[YANG] Required Keys Loaded: {self.REQUIRED_POLICY_KEYS}")
        
        # REST API 등록
        wsgi = kwargs['wsgi']
        wsgi.register(RestQoSController, { 'qos_app': self })

        # 모니터링 스레드
        self.monitor_thread = hub.spawn(self._monitor)
        
        # 통계 데이터 저장소
        self.prev_stats = {}
        
        # 가공된 네트워크 상태
        self.net_status = {
            "video_bps": 0, "download_bps": 0,
            "video_tx_bps": 0, "download_tx_bps": 0,
            "video_loss": 0, "total_bps": 0
        }

    # --- [NEW] Flow 추가 헬퍼 함수 ---
    def add_flow(self, datapath, priority, match, actions, meter_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        
        # Actions Instruction 생성
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        
        # Meter가 있다면 Instruction에 추가 (QoS 적용 시 사용)
        if meter_id:
            inst.insert(0, parser.OFPInstructionMeter(meter_id))
            
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                match=match, instructions=inst)
        datapath.send_msg(mod)

    # --- 기본 Flow 및 모니터링 Flow 설치 ---
    def install_base_flows(self, dp):
        parser = dp.ofproto_parser
        ofproto = dp.ofproto

        # 공통 Action: Normal Forwarding
        actions_normal = [parser.OFPActionOutput(ofproto.OFPP_NORMAL)]

        # 1. ARP, ICMP, 기본 통신 허용 (Priority 10)
        self.add_flow(dp, 10, parser.OFPMatch(eth_type=0x0806), actions_normal)
        self.add_flow(dp, 10, parser.OFPMatch(eth_type=0x0800, ip_proto=1), actions_normal)
        
        # 2. 모니터링용 Flow 설치 (Priority 5)
        # 통계 수집을 위해 트래픽을 구분하지만, 동작은 Normal Forwarding
        match_video = parser.OFPMatch(eth_type=0x0800, ip_proto=17, udp_dst=5001)
        self.add_flow(dp, 5, match_video, actions_normal)

        match_download = parser.OFPMatch(eth_type=0x0800, ip_proto=6, tcp_dst=5002)
        self.add_flow(dp, 5, match_download, actions_normal)

        # 3. Default: Normal Forwarding (Priority 0)
        self.add_flow(dp, 0, parser.OFPMatch(), actions_normal)

        self.logger.info(f"Initialized Switch: {dp.id} (Monitoring Flows Installed)")

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
            self.install_base_flows(dp)
        elif ev.state == DEAD_DISPATCHER:
            if dp.id in self.datapaths:
                del self.datapaths[dp.id]

    # --- 모니터링 (변경 없음) ---
    def _monitor(self):
        while True:
            for dp in self.datapaths.values():
                self._request_stats(dp)
            hub.sleep(1)

    def _request_stats(self, datapath):
        parser = datapath.ofproto_parser
        req = parser.OFPFlowStatsRequest(datapath)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        body = ev.msg.body
        
        vid_pkts = 0; vid_bytes = 0
        dl_pkts = 0; dl_bytes = 0

        # 모든 Flow Entry 통계 합산 (Priority 5 + Priority 100 QoS Flow)
        for stat in body:
            # Video (UDP 5001)
            if (stat.match.get('ip_proto') == 17 and stat.match.get('udp_dst') == 5001):
                vid_pkts += stat.packet_count
                vid_bytes += stat.byte_count
            
            # Download (TCP 5002)
            elif (stat.match.get('ip_proto') == 6 and stat.match.get('tcp_dst') == 5002):
                dl_pkts += stat.packet_count
                dl_bytes += stat.byte_count

        current_time = time.time()
        
        if dpid not in self.prev_stats:
            self.prev_stats[dpid] = {'vid_bytes': 0, 'dl_bytes': 0, 'time': current_time, 'vid_speed': 0, 'dl_speed': 0}

        prev = self.prev_stats[dpid]
        time_diff = max(0.001, current_time - prev['time'])
        
        vid_diff = max(0, vid_bytes - prev['vid_bytes'])
        dl_diff = max(0, dl_bytes - prev['dl_bytes'])

        self.prev_stats[dpid] = {
            'vid_bytes': vid_bytes, 'dl_bytes': dl_bytes, 'time': current_time,
            'vid_speed': vid_diff * 8 / time_diff, 
            'dl_speed': dl_diff * 8 / time_diff
        }

        s1 = self.prev_stats.get(1)
        s2 = self.prev_stats.get(2)

        if s1 and s2:
            self.net_status['video_bps'] = s1['vid_speed']
            self.net_status['download_bps'] = s1['dl_speed']
            self.net_status['total_bps'] = s1['vid_speed'] + s1['dl_speed']
            self.net_status['video_tx_bps'] = s2['vid_speed']
            self.net_status['download_tx_bps'] = s2['dl_speed']
            self.net_status['video_loss'] = max(0, s2['vid_speed'] - s1['vid_speed'])
            self.net_status['download_loss'] = max(0, s2['dl_speed'] - s1['dl_speed'])

    # --- QoS 정책 적용 (Meter 사용) ---
    def apply_policies(self, policies_list):
        # YANG 검증
        for policy in policies_list:
            if not self.REQUIRED_POLICY_KEYS.issubset(policy.keys()):
                missing = self.REQUIRED_POLICY_KEYS - set(policy.keys())
                self.logger.error(f"[YANG VALIDATION FAIL] Policy missing keys: {missing}. Policy: {policy}")
                continue

        policies = { p['name']: p for p in policies_list }
        print(f"[RYU] Applying Policies: {policies}")

        for dp in self.datapaths.values():
            ofp = dp.ofproto
            parser = dp.ofproto_parser
            actions_normal = [parser.OFPActionOutput(ofp.OFPP_NORMAL)]

            for name, pol in policies.items():
                # 1. Meter 설정 (속도 제한)
                meter_id = max(1, int(pol.get('priority', 1))) # Priority를 ID로 활용
                bw_mbps = int(pol.get('bandwidth-limit', 10))
                kbps = mbps_to_kbps(bw_mbps)
                
                bands = [parser.OFPMeterBandDrop(rate=kbps, burst_size=max(1000, int(kbps/10)))]
                req = parser.OFPMeterMod(datapath=dp, command=ofp.OFPMC_ADD, flags=ofp.OFPMF_KBPS, meter_id=meter_id, bands=bands)
                try:
                    dp.send_msg(req)
                except:
                    req = parser.OFPMeterMod(datapath=dp, command=ofp.OFPMC_MODIFY, flags=ofp.OFPMF_KBPS, meter_id=meter_id, bands=bands)
                    dp.send_msg(req)

                # 2. Flow 설정 (해당 Meter를 통과하도록 설정)
                # 요청하신대로 Download TCP만 별도로 강력하게 제어 가능
                if name == "video":
                    match = parser.OFPMatch(eth_type=0x0800, ip_proto=17, udp_dst=5001)
                elif name == "download":
                    match = parser.OFPMatch(eth_type=0x0800, ip_proto=6, tcp_dst=5002)
                else:
                    continue

                # Priority를 높여서(100+) 기존 Monitoring Flow(5)보다 먼저 적용되게 함
                # add_flow 함수를 사용하여 깔끔하게 적용
                prio = 100 + int(pol.get('priority', 1))
                self.add_flow(dp, prio, match, actions_normal, meter_id=meter_id)

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
            return Response(status=200, body=json.dumps({"msg": "OK"}), content_type='application/json', charset='utf-8')
        except Exception as e:
            return Response(status=500, body=str(e), charset='utf-8')

    @route('qos_stats', STATS_URL, methods=['GET'])
    def get_stats(self, req, **kwargs):
        return Response(content_type='application/json', body=json.dumps(self.qos_app.net_status), charset='utf-8')