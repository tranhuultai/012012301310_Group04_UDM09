"""File transfer tests: validation rules, end-to-end integrity, throughput, cancel cleanup."""
# pylint: disable=protected-access, too-many-locals, wrong-import-position

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from test_utils import TestMetrics, make_node, wait_for_session, wait_until  # noqa: E402
from network.transfer_manager import TransferManager, _sha256_file  # noqa: E402
from message.protocol import PacketType  # noqa: E402
from config import FILE_MAX_SIZE  # noqa: E402


def _run_now(fn) -> None:
    """schedule_gui shim — these tests have no GUI thread, so just call it."""
    fn()


class FileTransferTestSuite:
    """File validation rules plus real 2-node transfer integrity/throughput/cancel checks."""

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _connect_transfer_pair(self, port_a: int, port_b: int):
        """Connect two real nodes and wire a TransferManager on each side.

        Returns (tm_a, tm_b, node_a, node_b, target_peer_id) where
        target_peer_id is node_b's peer_id as seen by node_a — what
        tm_a.send_file() expects as its destination.
        """
        a_side, b_side = {}, {}
        ev_a, ev_b = threading.Event(), threading.Event()

        def on_a_connected(peer_id, tcp_addr):
            a_side["peer_id"] = peer_id
            a_side["tcp_addr"] = tcp_addr
            ev_a.set()

        def on_b_connected(peer_id, tcp_addr):
            b_side["peer_id"] = peer_id
            b_side["tcp_addr"] = tcp_addr
            ev_b.set()

        node_a = make_node(port_a, "TransferSender", on_connected=on_a_connected)
        node_b = make_node(port_b, "TransferReceiver", on_connected=on_b_connected)
        node_a.connect_to_peer("127.0.0.1", port_b)
        wait_for_session(ev_a, timeout=10.0)
        wait_for_session(ev_b, timeout=10.0)

        ctrl_a = MagicMock()
        ctrl_a._peer_id_to_tcp.return_value = a_side["tcp_addr"]
        ctrl_a.get_peer_info.return_value = {"username": "TransferReceiver"}

        ctrl_b = MagicMock()
        ctrl_b._peer_id_to_tcp.return_value = b_side["tcp_addr"]
        ctrl_b.get_peer_info.return_value = {"username": "TransferSender"}

        tm_a = TransferManager(node_a, ctrl_a, schedule_gui=_run_now)
        tm_b = TransferManager(node_b, ctrl_b, schedule_gui=_run_now)

        return tm_a, tm_b, node_a, node_b, a_side["peer_id"]

    def _measure_transfer(self, port_a: int, port_b: int, size_kb: int, tmp_dir: str) -> dict:
        """Send a size_kb random file end-to-end; return timing and integrity info."""
        src_path = Path(tmp_dir) / f"payload_{size_kb}kb_{uuid.uuid4().hex[:6]}.zip"
        src_path.write_bytes(os.urandom(size_kb * 1024))
        expected_sha = _sha256_file(src_path)

        tm_a, tm_b, node_a, node_b, target_peer_id = self._connect_transfer_pair(port_a, port_b)
        try:
            complete_event = threading.Event()
            complete_info: dict = {}

            def on_complete(_tid, success, message):
                complete_info["success"] = success
                complete_info["message"] = message
                complete_event.set()
            tm_b.on_transfer_complete = on_complete

            meta_event = threading.Event()
            meta_holder: dict = {}

            def on_meta(meta, _peer_name):
                meta_holder["meta"] = meta
                meta_event.set()
            tm_b.on_file_meta = on_meta

            start = time.monotonic()
            ok, tid = tm_a.send_file(str(src_path), target_peer_id)
            if not ok:
                raise RuntimeError(f"send_file failed: {tid}")
            wait_until(meta_event.is_set, timeout=10.0)
            tm_b.request_download(meta_holder["meta"].transfer_id)
            wait_until(complete_event.is_set, timeout=60.0)
            elapsed = time.monotonic() - start

            entry = tm_b._transfers.get(tid, {})
            save_path = entry.get("save_path")
            sha_match = bool(
                complete_info.get("success")
                and save_path is not None
                and save_path.exists()
                and _sha256_file(save_path) == expected_sha
            )
            return {
                "elapsed_s":       elapsed,
                "throughput_kb_s": round(size_kb / elapsed, 2) if elapsed > 0 else 0.0,
                "sha256_match":    sha_match,
            }
        finally:
            node_a.stop_server()
            node_b.stop_server()

    # ------------------------------------------------------------------ #
    # Tests                                                                #
    # ------------------------------------------------------------------ #

    def test_file_validation_rules(self) -> dict:
        """Check send_file rejects each invalid input with the expected message."""
        node = MagicMock()
        node.peers_lock = threading.RLock()
        node.peer_sessions = {}
        node.identity_manager.get_peer_id.return_value = "local_peer_id"
        ctrl = MagicMock()
        ctrl._peer_id_to_tcp.return_value = "127.0.0.1:18000"
        ctrl.get_peer_info.return_value = {"username": "Peer"}

        tmp_dir = tempfile.mkdtemp(prefix="p2pchat_ft_validate_")
        try:
            tm = TransferManager(node, ctrl, schedule_gui=_run_now)
            details = []
            passed = 0

            bad_ext = Path(tmp_dir) / "malware.exe"
            bad_ext.write_bytes(b"data")
            ok, msg = tm.send_file(str(bad_ext), "peer")
            check = ok is False and "Unsupported" in msg
            details.append(f"1. disallowed extension -> {msg!r}: {'OK' if check else 'FAIL'}")
            passed += check

            ok, msg = tm.send_file(str(Path(tmp_dir) / "ghost.pdf"), "peer")
            check = ok is False and "not found" in msg
            details.append(f"2. missing file -> {msg!r}: {'OK' if check else 'FAIL'}")
            passed += check

            empty = Path(tmp_dir) / "empty.pdf"
            empty.write_bytes(b"")
            ok, msg = tm.send_file(str(empty), "peer")
            check = ok is False and "empty" in msg.lower()
            details.append(f"3. empty file -> {msg!r}: {'OK' if check else 'FAIL'}")
            passed += check

            big = Path(tmp_dir) / "big.pdf"
            with open(big, "wb") as big_fh:
                big_fh.truncate(FILE_MAX_SIZE + 1)
            ok, msg = tm.send_file(str(big), "peer")
            check = ok is False and "large" in msg.lower()
            details.append(f"4. oversized file -> {msg!r}: {'OK' if check else 'FAIL'}")
            passed += check

            ctrl._peer_id_to_tcp.return_value = None
            valid = Path(tmp_dir) / "doc.pdf"
            valid.write_bytes(b"valid content")
            ok, msg = tm.send_file(str(valid), "peer")
            check = ok is False and "connected" in msg.lower()
            details.append(f"5. peer not connected -> {msg!r}: {'OK' if check else 'FAIL'}")
            passed += check

            result = {
                "rules_tested": 5,
                "passed": passed,
                "failed": 5 - passed,
                "details": details,
            }
            print(f"[FILETRANSFER] file_validation_rules: {result}")
            return result
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_file_integrity_e2e(self, file_size_kb: int = 512) -> dict:
        """Transfer a random file over real sockets and verify SHA-256 on arrival."""
        tmp_dir = tempfile.mkdtemp(prefix="p2pchat_ft_e2e_")
        try:
            r = self._measure_transfer(18000, 18001, file_size_kb, tmp_dir)
            result = {
                "file_size_kb":       file_size_kb,
                "transfer_time_s":    round(r["elapsed_s"], 3),
                "throughput_kb_per_s": r["throughput_kb_s"],
                "sha256_match":       r["sha256_match"],
            }
            print(f"[FILETRANSFER] file_integrity_e2e: {result}")
            return result
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_file_throughput_scaling(self, sizes_kb: "list[int] | None" = None) -> dict:
        """Transfer each size 3 times and average the throughput."""
        sizes_kb = sizes_kb or [64, 256, 1024, 4096]
        tmp_dir = tempfile.mkdtemp(prefix="p2pchat_ft_scale_")
        try:
            result = {}
            for size_kb in sizes_kb:
                times, throughputs = [], []
                for _ in range(3):
                    r = self._measure_transfer(18008, 18009, size_kb, tmp_dir)
                    times.append(r["elapsed_s"])
                    throughputs.append(r["throughput_kb_s"])
                result[str(size_kb)] = {
                    "avg_throughput_kb_s": round(sum(throughputs) / len(throughputs), 2),
                    "min_s": round(min(times), 3),
                    "max_s": round(max(times), 3),
                }
            print(f"[FILETRANSFER] throughput_scaling: {result}")
            return result
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_transfer_cancel_cleanup(self) -> dict:
        """Cancel a large transfer mid-flight and confirm the .part handle is released."""
        tmp_dir = tempfile.mkdtemp(prefix="p2pchat_ft_cancel_")
        try:
            src_path = Path(tmp_dir) / "big.zip"
            src_path.write_bytes(os.urandom(4 * 1024 * 1024))  # 4 MB

            tm_a, tm_b, node_a, node_b, target_peer_id = self._connect_transfer_pair(18004, 18005)
            try:
                # Loopback transfers a 4 MB file in ~0.2s, too fast to reliably
                # catch mid-flight — throttle chunk sends so cancel has a real window.
                real_send = node_a.send_raw_packet

                def throttled_send(packet, tcp_addr):
                    if packet.get("type") == PacketType.FILE_CHUNK:
                        time.sleep(0.05)
                    return real_send(packet, tcp_addr)
                node_a.send_raw_packet = throttled_send

                meta_event = threading.Event()
                meta_holder: dict = {}

                def on_meta(meta, _peer_name):
                    meta_holder["meta"] = meta
                    meta_event.set()
                tm_b.on_file_meta = on_meta

                cancel_event = threading.Event()

                def on_complete(_tid, _success, _message):
                    cancel_event.set()
                tm_b.on_transfer_complete = on_complete

                _ok, tid = tm_a.send_file(str(src_path), target_peer_id)
                wait_until(meta_event.is_set, timeout=10.0)
                tm_b.request_download(meta_holder["meta"].transfer_id)

                time.sleep(0.3)  # a few throttled chunks have gone out, transfer still open
                receiving_at_cancel = tm_b._transfers.get(tid, {}).get("state") == "receiving"
                tm_a.cancel_transfer(tid)
                wait_until(cancel_event.is_set, timeout=5.0)

                entry = tm_b._transfers.get(tid, {})
                part_path = entry.get("part_path")
                handle_released = "part_fh" not in entry

                # On Windows, unlink() fails if a handle on the file is still open —
                # this doubles as a real check that the receiver released the file.
                unlink_ok = True
                if part_path is not None and part_path.exists():
                    try:
                        part_path.unlink()
                    except OSError:
                        unlink_ok = False

                result = {
                    "cancelled_mid_transfer": receiving_at_cancel,
                    "cancelled_ok":           cancel_event.is_set(),
                    "part_file_cleaned":      handle_released and unlink_ok,
                }
                print(f"[FILETRANSFER] transfer_cancel_cleanup: {result}")
                return result
            finally:
                node_a.stop_server()
                node_b.stop_server()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def run_all(self) -> dict:
        """Run every file-transfer test and collect the results into one dict."""
        return {
            "validation_rules":   self.test_file_validation_rules(),
            "integrity_e2e":      self.test_file_integrity_e2e(),
            "throughput_scaling": self.test_file_throughput_scaling(),
            "cancel_cleanup":     self.test_transfer_cancel_cleanup(),
        }


if __name__ == "__main__":
    out_dir = os.path.join(os.path.dirname(__file__), "results", "metrics")
    os.makedirs(out_dir, exist_ok=True)

    metrics = TestMetrics()
    metrics.start()
    results = FileTransferTestSuite().run_all()
    results["system_metrics"] = metrics.stop()

    out_path = os.path.join(out_dir, "file_transfer_test_results.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(f"[FILETRANSFER] Results written to {out_path}")
