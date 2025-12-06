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

# Import YANG model parser
from yang_parser import get_required_policy_keys

# --- Configuration ---
# Matches the Decision Engine (Client) endpoint URL (http://.../qos/qos-policies)
REST_URL = '/qos/qos-policies'
STATS_URL = '/stats'

# Port mapping (Mininet: vSrv->5001, dSrv->5002)
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

        # Load required keys via the YANG parser (name, priority, bandwidth-limit)
        self.REQUIRED_POLICY_KEYS = get_required_policy_keys()
        self.logger.info(f"[YANG] Required Keys Loaded: {self.REQUIRED_POLICY_KEYS}")

        # Register REST API endpoints
        wsgi = kwargs['wsgi']
        wsgi.register(RestQoSController, { 'qos_app': self })

        # Monitoring thread
        self.monitor_thread = hub.spawn(self._monitor)

        # Statistics storage
        self.prev_stats = {}

        # Processed network state
        self.net_status = {
            "video_bps": 0, "download_bps": 0,
            "video_tx_bps": 0, "download_tx_bps": 0,
            "video_loss": 0, "total_bps": 0
        }

    # --- Flow helper ---
    def add_flow(self, datapath, priority, match, actions, meter_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Build Actions Instruction
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]

        # Attach meter when provided (used for QoS)
        if meter_id:
            inst.insert(0, parser.OFPInstructionMeter(meter_id))

        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                match=match, instructions=inst)
        datapath.send_msg(mod)

    # --- Base and monitoring flows ---
    def install_base_flows(self, dp):
        parser = dp.ofproto_parser
        ofproto = dp.ofproto

        # Shared action: Normal forwarding
        actions_normal = [parser.OFPActionOutput(ofproto.OFPP_NORMAL)]

        # 1. Allow ARP and ICMP (Priority 10)
        self.add_flow(dp, 10, parser.OFPMatch(eth_type=0x0806), actions_normal)
        self.add_flow(dp, 10, parser.OFPMatch(eth_type=0x0800, ip_proto=1), actions_normal)

        # 2. Monitoring flows (Priority 5)
        # Separate traffic for statistics while still forwarding normally
        match_video = parser.OFPMatch(eth_type=0x0800, ip_proto=6, tcp_dst=5001)
        self.add_flow(dp, 5, match_video, actions_normal)

        match_download = parser.OFPMatch(eth_type=0x0800, ip_proto=6, tcp_dst=5002)
        self.add_flow(dp, 5, match_download, actions_normal)

        # 3. Default: Normal forwarding (Priority 0)
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

    # --- Monitoring ---
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

        # Aggregate statistics from all flow entries (Priority 5 + Priority 100 QoS Flow)
        for stat in body:
            # Video (TCP ABR 5001)
            if (stat.match.get('ip_proto') == 6 and stat.match.get('tcp_dst') == 5001):
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

    # --- Apply QoS policies (meter-based) ---
    def apply_policies(self, policies_list):
        # YANG validation
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
                # 1. Configure meter (rate limiting)
                meter_id = max(1, int(pol.get('priority', 1)))  # Use priority as meter ID
                bw_mbps = int(pol.get('bandwidth-limit', 10))
                kbps = mbps_to_kbps(bw_mbps)

                bands = [parser.OFPMeterBandDrop(rate=kbps, burst_size=max(1000, int(kbps/10)))]

                # First ADD (creates meter if missing)
                req_add = parser.OFPMeterMod(datapath=dp, command=ofp.OFPMC_ADD, flags=ofp.OFPMF_KBPS, meter_id=meter_id, bands=bands)
                dp.send_msg(req_add)

                # Then MODIFY (updates meter if it already exists)
                req_mod = parser.OFPMeterMod(datapath=dp, command=ofp.OFPMC_MODIFY, flags=ofp.OFPMF_KBPS, meter_id=meter_id, bands=bands)
                dp.send_msg(req_mod)

                # Configure flow to pass through the meter
                # Allows targeted control of download TCP separately
                if name == "video":
                    match = parser.OFPMatch(eth_type=0x0800, ip_proto=6, tcp_dst=5001)
                elif name == "download":
                    match = parser.OFPMatch(eth_type=0x0800, ip_proto=6, tcp_dst=5002)
                else:
                    continue

                # Use higher priority (100+) so it precedes monitoring flows (5)
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
