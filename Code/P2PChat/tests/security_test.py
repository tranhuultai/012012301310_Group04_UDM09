"""Security tests: block enforcement, replay-attack rejection, TOFU transitions."""
# pylint: disable=too-many-locals, duplicate-code, wrong-import-position

import json
import os
import sys
import threading
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from test_utils import TestMetrics, make_node, wait_for_session  # noqa: E402
from message.protocol import PacketType  # noqa: E402
from trust.tofu_engine import TOFUEngine  # noqa: E402
from trust.trust_state import TrustState  # noqa: E402


class SecurityTestSuite:
    """Blocked-peer enforcement, replay protection, and TOFU state machine checks."""

    def test_blocked_peer_cannot_send(self, attempts: int = 20) -> dict:
        """Block a connected peer and confirm no further messages get through."""
        port_a, port_b = 17000, 17001
        received: list = []
        received_lock = threading.Lock()
        connected_event = threading.Event()
        peer_id_holder: dict = {}

        def on_b_message(_peer_id, _sender, payload):
            with received_lock:
                received.append(payload)

        def on_b_connected(peer_id, _tcp_addr):
            peer_id_holder["id"] = peer_id
            connected_event.set()

        node_a = make_node(port_a, "BlockedSender")
        node_b = make_node(port_b, "Blocker", on_message=on_b_message, on_connected=on_b_connected)
        try:
            node_a.connect_to_peer("127.0.0.1", port_b)
            wait_for_session(connected_event, timeout=10.0)
            peer_a_id = peer_id_holder["id"]

            # Direct connect_to_peer bypasses UDP discovery, so TOFU has no
            # record yet — seed one so block_peer() has something to block.
            node_b.tofu.add_peer(peer_a_id, "test-fingerprint")
            blocked = node_b.block_peer(peer_a_id)

            for i in range(attempts):
                node_a.send_message(f"blocked-{i}", f"127.0.0.1:{port_b}")
            time.sleep(0.5)  # let node_b's receive thread drain and drop everything

            with received_lock:
                received_after_block = len(received)

            result = {
                "blocked": blocked,
                "attempts": attempts,
                "received_after_block": received_after_block,
                "block_effective": bool(blocked and received_after_block == 0),
            }
            print(f"[SECURITY] blocked_peer_cannot_send: {result}")
            return result
        finally:
            node_a.stop_server()
            node_b.stop_server()

    def test_replay_attack_detection(self, replay_count: int = 10) -> dict:
        """Resend the same message_id repeatedly and confirm duplicates are dropped."""
        port_a, port_b = 17002, 17003
        received: list = []
        received_lock = threading.Lock()
        connected_event = threading.Event()

        def on_b_message(_peer_id, _sender, payload):
            with received_lock:
                received.append(payload)

        def on_a_connected(_peer_id, _tcp_addr):
            connected_event.set()

        node_a = make_node(port_a, "ReplaySender", on_connected=on_a_connected)
        node_b = make_node(port_b, "ReplayReceiver", on_message=on_b_message)
        try:
            node_a.connect_to_peer("127.0.0.1", port_b)
            wait_for_session(connected_event, timeout=10.0)

            tcp_addr = f"127.0.0.1:{port_b}"
            session = node_a.peer_sessions.get(tcp_addr, {})
            crypto = session.get("crypto")
            packet = node_a.protocol_handler.create_packet(
                PacketType.MESSAGE, node_a.username, "original", crypto=crypto,
            )

            node_a.send_raw_packet(packet, tcp_addr)  # the one legitimate send
            time.sleep(0.3)

            for _ in range(replay_count):
                node_a.send_raw_packet(packet, tcp_addr)  # same message_id every time
            time.sleep(0.5)

            with received_lock:
                total_received = len(received)
            replays_accepted = max(0, total_received - 1)

            result = {
                "original_sent":     1,
                "replays_attempted": replay_count,
                "replays_accepted":  replays_accepted,
                "protection_ok":     replays_accepted == 0,
            }
            print(f"[SECURITY] replay_attack_detection: {result}")
            return result
        finally:
            node_a.stop_server()
            node_b.stop_server()

    def test_tofu_state_transitions(self) -> dict:
        """Walk a single peer through NEW -> VERIFIED -> MISMATCH -> TRUSTED -> BLOCKED."""
        tofu = TOFUEngine(profile=f"sec_test_{uuid.uuid4().hex[:8]}")
        peer_id = f"peer_{uuid.uuid4().hex[:16]}"
        fp_a, fp_b = "AA:BB:CC:DD", "11:22:33:44"

        details = []
        passed = 0

        def check(step: str, actual: str, expected: str) -> None:
            nonlocal passed
            ok = actual == expected
            details.append(f"{step}: got {actual}, expected {expected} "
                            f"-> {'OK' if ok else 'FAIL'}")
            passed += ok

        check("1. first contact",     tofu.verify_peer(peer_id, fp_a), TrustState.NEW)
        check("2. same fingerprint",  tofu.verify_peer(peer_id, fp_a), TrustState.VERIFIED)
        check("3. changed fingerprint", tofu.verify_peer(peer_id, fp_b), TrustState.MISMATCH)

        tofu.accept_mismatch(peer_id, fp_b)
        check("4. accept_mismatch", tofu.get_trust_state(peer_id), TrustState.TRUSTED)

        tofu.block_peer(peer_id)
        check("5. block_peer", "BLOCKED" if tofu.is_blocked(peer_id) else "NOT_BLOCKED", "BLOCKED")

        result = {
            "transitions_tested": 5,
            "passed": passed,
            "failed": 5 - passed,
            "details": details,
        }
        print(f"[SECURITY] tofu_state_transitions: {result}")
        return result

    def test_fingerprint_mismatch_detection(self, peer_count: int = 10) -> dict:
        """Run peer_count independent NEW->VERIFIED->MISMATCH sequences and score detection."""
        tofu = TOFUEngine(profile=f"sec_test_{uuid.uuid4().hex[:8]}")
        detected = 0
        for i in range(peer_count):
            peer_id = f"peer_{uuid.uuid4().hex[:16]}"
            fp_a, fp_b = f"fp_a_{i}", f"fp_b_{i}"
            tofu.verify_peer(peer_id, fp_a)           # NEW
            tofu.verify_peer(peer_id, fp_a)           # VERIFIED
            state = tofu.verify_peer(peer_id, fp_b)   # expect MISMATCH
            if state == TrustState.MISMATCH:
                detected += 1

        result = {
            "peers_tested":       peer_count,
            "mismatch_detected":  detected,
            "detection_rate_pct": round(detected / peer_count * 100, 2) if peer_count else 0.0,
        }
        print(f"[SECURITY] fingerprint_mismatch_detection: {result}")
        return result

    def run_all(self) -> dict:
        """Run every security test and collect the results into one dict."""
        return {
            "blocked_peer":         self.test_blocked_peer_cannot_send(),
            "replay_attack":        self.test_replay_attack_detection(),
            "tofu_transitions":     self.test_tofu_state_transitions(),
            "fingerprint_mismatch": self.test_fingerprint_mismatch_detection(),
        }


if __name__ == "__main__":
    out_dir = os.path.join(os.path.dirname(__file__), "results", "metrics")
    os.makedirs(out_dir, exist_ok=True)

    metrics = TestMetrics()
    metrics.start()
    results = SecurityTestSuite().run_all()
    results["system_metrics"] = metrics.stop()

    out_path = os.path.join(out_dir, "security_test_results.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(f"[SECURITY] Results written to {out_path}")
