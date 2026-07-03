"""Reliability tests: disconnect/reconnect behaviour and session cleanup."""
# pylint: disable=too-many-locals, duplicate-code, wrong-import-position

import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from test_utils import TestMetrics, make_node, wait_for_session, wait_until  # noqa: E402


class ReliabilityTestSuite:
    """Reconnect success rate, message delivery after reconnect, session cleanup."""

    def test_reconnect_success_rate(self, attempts: int = 10) -> dict:
        """Repeatedly connect/disconnect two nodes and measure handshake success."""
        port_a, port_b = 16000, 16001
        connected_event = threading.Event()
        peer_id_holder: dict = {}

        def on_a_connected(peer_id, _tcp_addr):
            peer_id_holder["id"] = peer_id
            connected_event.set()

        node_a = make_node(port_a, "ReconnectA", on_connected=on_a_connected)
        node_b = make_node(port_b, "ReconnectB")
        try:
            successes = 0
            reconnect_ms = []
            for i in range(attempts):
                connected_event.clear()
                start = time.monotonic()
                if not node_a.connect_to_peer("127.0.0.1", port_b):
                    continue
                if wait_for_session(connected_event, timeout=10.0):
                    successes += 1
                    reconnect_ms.append((time.monotonic() - start) * 1000)
                    node_a.send_message(f"ping-{i}", f"127.0.0.1:{port_b}")
                    node_a.disconnect_peer(peer_id_holder["id"])
                    wait_until(lambda: f"127.0.0.1:{port_b}" not in node_a.peers, timeout=2.0)

            result = {
                "attempts": attempts,
                "successes": successes,
                "success_rate_pct": round(successes / attempts * 100, 2) if attempts else 0.0,
                "avg_reconnect_ms": round(sum(reconnect_ms) / len(reconnect_ms), 2)
                                    if reconnect_ms else 0.0,
            }
            print(f"[RELIABILITY] reconnect_success_rate: {result}")
            return result
        finally:
            node_a.stop_server()
            node_b.stop_server()

    def test_messages_after_reconnect(self, message_count: int = 50) -> dict:
        """Send messages before/after a disconnect and confirm none are lost post-reconnect."""
        port_a, port_b = 16002, 16003
        received: list = []
        received_lock = threading.Lock()
        connected_event = threading.Event()
        peer_id_holder: dict = {}

        def on_b_message(_peer_id, _sender, payload):
            with received_lock:
                received.append(payload)

        def on_a_connected(peer_id, _tcp_addr):
            peer_id_holder["id"] = peer_id
            connected_event.set()

        node_a = make_node(port_a, "MsgA", on_connected=on_a_connected)
        node_b = make_node(port_b, "MsgB", on_message=on_b_message)
        try:
            node_a.connect_to_peer("127.0.0.1", port_b)
            wait_for_session(connected_event, timeout=10.0)

            sent_before = 10
            for i in range(sent_before):
                node_a.send_message(f"before-{i}", f"127.0.0.1:{port_b}")
            wait_until(lambda: len(received) >= sent_before, timeout=5.0)

            connected_event.clear()
            node_a.disconnect_peer(peer_id_holder["id"])
            wait_until(lambda: f"127.0.0.1:{port_b}" not in node_a.peers, timeout=2.0)

            node_a.connect_to_peer("127.0.0.1", port_b)
            wait_for_session(connected_event, timeout=10.0)

            with received_lock:
                received.clear()  # only messages sent after the reconnect count here
            for i in range(message_count):
                node_a.send_message(f"after-{i}", f"127.0.0.1:{port_b}")
            wait_until(lambda: len(received) >= message_count, timeout=10.0)

            with received_lock:
                received_after = len(received)

            loss = message_count - received_after
            result = {
                "sent_before": sent_before,
                "sent_after": message_count,
                "received_after": received_after,
                "loss_rate_pct": round(loss / message_count * 100, 2) if message_count else 0.0,
            }
            print(f"[RELIABILITY] messages_after_reconnect: {result}")
            return result
        finally:
            node_a.stop_server()
            node_b.stop_server()

    def test_session_cleanup_on_disconnect(self, peer_count: int = 5) -> dict:
        """Connect several peers to one node and verify each session is fully cleaned up."""
        main_port = 16004
        client_base_port = 16005
        main_node = make_node(main_port, "MainNode")
        clients = []
        try:
            connected_events = []
            for i in range(peer_count):
                ev = threading.Event()

                def on_connected(_peer_id, _tcp_addr, ev=ev):
                    ev.set()

                client = make_node(client_base_port + i, f"Client{i}", on_connected=on_connected)
                clients.append(client)
                connected_events.append(ev)
                client.connect_to_peer("127.0.0.1", main_port)

            connected_ok = sum(1 for ev in connected_events if wait_for_session(ev, timeout=10.0))
            wait_until(lambda: len(main_node.peer_sessions) >= connected_ok, timeout=5.0)

            cleanup_ok = 0
            cleanup_failed = 0
            for client in clients:
                client_peer_id = client.identity_manager.get_peer_id()
                closed = main_node.disconnect_peer(client_peer_id)
                settled = wait_until(
                    lambda pid=client_peer_id: all(
                        s.get("peer_id") != pid for s in main_node.peer_sessions.values()
                    ),
                    timeout=3.0,
                )
                if closed and settled:
                    cleanup_ok += 1
                else:
                    cleanup_failed += 1

            result = {
                "peers_connected": connected_ok,
                "cleanup_ok": cleanup_ok,
                "cleanup_failed": cleanup_failed,
            }
            print(f"[RELIABILITY] session_cleanup_on_disconnect: {result}")
            return result
        finally:
            for client in clients:
                client.stop_server()
            main_node.stop_server()

    def run_all(self) -> dict:
        """Run every reliability test and collect the results into one dict."""
        return {
            "reconnect":           self.test_reconnect_success_rate(),
            "msg_after_reconnect": self.test_messages_after_reconnect(),
            "session_cleanup":     self.test_session_cleanup_on_disconnect(),
        }


if __name__ == "__main__":
    out_dir = os.path.join(os.path.dirname(__file__), "results", "metrics")
    os.makedirs(out_dir, exist_ok=True)

    metrics = TestMetrics()
    metrics.start()
    results = ReliabilityTestSuite().run_all()
    results["system_metrics"] = metrics.stop()

    out_path = os.path.join(out_dir, "reliability_test_results.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(f"[RELIABILITY] Results written to {out_path}")
