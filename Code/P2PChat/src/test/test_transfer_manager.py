"""Unit tests for network/transfer_manager.py.

Strategy: test every pure function and the TransferMeta DTO directly
without requiring a real network node, TCP sockets, or GUI.

Functions under test:
    _sha256_file          — file hashing
    _mime_for_ext         — MIME type lookup
    TransferMeta          — DTO serialisation / deserialisation
    TransferManager._total_chunks   — chunk-count arithmetic
    TransferManager._safe_save_path — collision-safe path generation

The send_file validation logic is tested by creating real temporary files
and verifying the (False, error_message) return values.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import the standalone module-level helpers directly.
from network.transfer_manager import (
    TransferMeta,
    TransferManager,
    _sha256_file,
    _mime_for_ext,
)
from config import FILE_CHUNK_SIZE, FILE_MAX_SIZE, FILE_ALLOWED_EXT  # noqa

# Testing private methods requires direct access — suppress pylint protected-access.
# pylint: disable=protected-access


# ── _sha256_file ───────────────────────────────────────────────────────────

class TestSha256File:
    """_sha256_file must produce consistent, correct SHA-256 digests."""

    def test_known_content(self, tmp_path: Path) -> None:
        """Output matches hashlib reference for known bytes."""
        data   = b"P2PChat file transfer integrity test"
        target = tmp_path / "sample.txt"
        target.write_bytes(data)

        expected = hashlib.sha256(data).hexdigest()
        assert _sha256_file(target) == expected

    def test_empty_file(self, tmp_path: Path) -> None:
        """Empty file produces the canonical SHA-256 of zero bytes."""
        target = tmp_path / "empty.txt"
        target.write_bytes(b"")
        assert _sha256_file(target) == hashlib.sha256(b"").hexdigest()

    def test_large_file(self, tmp_path: Path) -> None:
        """Files larger than the 64 KB read block are hashed correctly."""
        data   = b"x" * (FILE_CHUNK_SIZE + 1)  # crosses internal block boundary
        target = tmp_path / "large.bin"
        target.write_bytes(data)

        expected = hashlib.sha256(data).hexdigest()
        assert _sha256_file(target) == expected

    def test_returns_64_char_hex(self, tmp_path: Path) -> None:
        """Output is always a 64-character lowercase hex string."""
        target = tmp_path / "hex.txt"
        target.write_bytes(b"hello")
        result = _sha256_file(target)
        assert len(result) == 64
        assert result == result.lower()


# ── _mime_for_ext ──────────────────────────────────────────────────────────

class TestMimeForExt:
    """_mime_for_ext must return the correct MIME type for each allowed extension."""

    @pytest.mark.parametrize("ext,expected", [
        (".pdf",  "application/pdf"),
        (".txt",  "text/plain"),
        (".png",  "image/png"),
        (".jpg",  "image/jpeg"),
        (".jpeg", "image/jpeg"),
        (".zip",  "application/zip"),
        (".docx", "application/vnd.openxmlformats-officedocument"
                  ".wordprocessingml.document"),
    ])
    def test_known_extensions(self, ext: str, expected: str) -> None:
        """Each supported extension must map to its expected MIME type."""
        assert _mime_for_ext(ext) == expected

    def test_unknown_extension_fallback(self) -> None:
        """Unknown extensions get the generic octet-stream type."""
        assert _mime_for_ext(".xyz") == "application/octet-stream"
        assert _mime_for_ext("")    == "application/octet-stream"


# ── TransferMeta ───────────────────────────────────────────────────────────

class TestTransferMeta:
    """TransferMeta DTO must serialise and deserialise without data loss."""

    def _make_meta(self) -> TransferMeta:
        return TransferMeta(
            transfer_id = "tid-001",
            filename    = "report.pdf",
            filesize    = 204800,
            mime_type   = "application/pdf",
            sha256      = "a" * 64,
            sender_id   = "sender_peer_id_" + "0" * 48,
            receiver_id = "receiver_peer_id_" + "0" * 46,
            timestamp   = 1700000000.0,
        )

    def test_to_dict_round_trip(self) -> None:
        """to_dict then from_dict reproduces the original object exactly."""
        meta  = self._make_meta()
        d     = meta.to_dict()
        meta2 = TransferMeta.from_dict(d)

        assert meta2.transfer_id == meta.transfer_id
        assert meta2.filename    == meta.filename
        assert meta2.filesize    == meta.filesize
        assert meta2.mime_type   == meta.mime_type
        assert meta2.sha256      == meta.sha256
        assert meta2.sender_id   == meta.sender_id
        assert meta2.receiver_id == meta.receiver_id
        assert meta2.timestamp   == meta.timestamp

    def test_to_dict_contains_all_fields(self) -> None:
        """to_dict must include every __slots__ field."""
        meta = self._make_meta()
        d    = meta.to_dict()
        for slot in TransferMeta.__slots__:
            assert slot in d, f"Missing field: {slot}"

    def test_from_dict_filesize_coercion(self) -> None:
        """from_dict coerces filesize to int even when stored as string."""
        d = self._make_meta().to_dict()
        d["filesize"] = "204800"   # simulate JSON string
        meta = TransferMeta.from_dict(d)
        assert isinstance(meta.filesize, int)
        assert meta.filesize == 204800

    def test_from_dict_optional_fields(self) -> None:
        """from_dict tolerates missing optional fields with safe defaults."""
        d = {
            "transfer_id": "tid-002",
            "filename":    "test.txt",
            "filesize":    100,
            "sha256":      "b" * 64,
        }
        meta = TransferMeta.from_dict(d)
        assert meta.mime_type   == "application/octet-stream"
        assert meta.sender_id   == ""
        assert meta.receiver_id == ""
        assert isinstance(meta.timestamp, float)

    def test_from_dict_rejects_missing_required_fields(self) -> None:
        """from_dict raises KeyError when a required field is absent."""
        with pytest.raises(KeyError):
            TransferMeta.from_dict({"filename": "test.txt"})   # no transfer_id


# ── TransferManager._total_chunks ─────────────────────────────────────────

class TestTotalChunks:
    """_total_chunks must compute the correct ceil-division with a min of 1."""

    def test_exact_multiple(self) -> None:
        """Exactly 2 × CHUNK_SIZE bytes → 2 chunks."""
        assert TransferManager._total_chunks(FILE_CHUNK_SIZE * 2) == 2

    def test_one_byte_over(self) -> None:
        """One byte over a chunk boundary requires an extra chunk."""
        assert TransferManager._total_chunks(FILE_CHUNK_SIZE + 1) == 2

    def test_empty_file(self) -> None:
        """0 bytes → min 1 chunk (prevents divide-by-zero and empty sequences)."""
        assert TransferManager._total_chunks(0) == 1

    def test_single_byte(self) -> None:
        """1 byte → 1 chunk."""
        assert TransferManager._total_chunks(1) == 1

    def test_exactly_one_chunk(self) -> None:
        """FILE_CHUNK_SIZE bytes → exactly 1 chunk."""
        assert TransferManager._total_chunks(FILE_CHUNK_SIZE) == 1

    def test_max_file_size(self) -> None:
        """10 MB (max allowed) → expected chunk count."""
        expected = (FILE_MAX_SIZE + FILE_CHUNK_SIZE - 1) // FILE_CHUNK_SIZE
        assert TransferManager._total_chunks(FILE_MAX_SIZE) == expected


# ── TransferManager._safe_save_path ───────────────────────────────────────

class TestSafeSavePath:
    """_safe_save_path must never return a path that already exists."""

    def _make_manager(self, _tmp_path: Path) -> TransferManager:
        """Create a minimal TransferManager stub with patched download dir."""
        mgr = object.__new__(TransferManager)  # skip __init__
        mgr._node         = MagicMock()
        mgr._ctrl         = MagicMock()
        mgr._schedule_gui = lambda fn: None
        mgr._transfers    = {}
        mgr._lock         = __import__("threading").Lock()
        # All callback hooks
        for attr in ("on_transfer_started", "on_transfer_progress",
                     "on_transfer_complete", "on_transfer_error",
                     "on_file_meta"):
            setattr(mgr, attr, None)
        return mgr

    def test_no_collision(self, tmp_path: Path) -> None:
        """When the target does not exist, returns the exact filename."""
        mgr = self._make_manager(tmp_path)
        with patch("network.transfer_manager.FILE_DOWNLOAD_DIR",
                   str(tmp_path)):
            path = mgr._safe_save_path("report.pdf")
        assert path.name == "report.pdf"
        assert not path.exists()

    def test_one_collision(self, tmp_path: Path) -> None:
        """When report.pdf exists, returns report_1.pdf."""
        (tmp_path / "report.pdf").write_text("existing")
        mgr = self._make_manager(tmp_path)
        with patch("network.transfer_manager.FILE_DOWNLOAD_DIR",
                   str(tmp_path)):
            path = mgr._safe_save_path("report.pdf")
        assert path.name == "report_1.pdf"

    def test_two_collisions(self, tmp_path: Path) -> None:
        """When both report.pdf and report_1.pdf exist, returns report_2.pdf."""
        (tmp_path / "report.pdf").write_text("existing")
        (tmp_path / "report_1.pdf").write_text("also existing")
        mgr = self._make_manager(tmp_path)
        with patch("network.transfer_manager.FILE_DOWNLOAD_DIR",
                   str(tmp_path)):
            path = mgr._safe_save_path("report.pdf")
        assert path.name == "report_2.pdf"

    def test_preserves_extension(self, tmp_path: Path) -> None:
        """Collision renaming keeps the original file extension."""
        (tmp_path / "image.png").write_text("x")
        mgr = self._make_manager(tmp_path)
        with patch("network.transfer_manager.FILE_DOWNLOAD_DIR",
                   str(tmp_path)):
            path = mgr._safe_save_path("image.png")
        assert path.suffix == ".png"


# ── send_file validation (no network) ─────────────────────────────────────

class TestSendFileValidation:
    """send_file must reject invalid inputs before attempting any I/O."""

    def _make_manager_with_node(self, _tmp_path: Path) -> TransferManager:
        """Minimal manager where _ctrl._peer_id_to_tcp returns None (not connected)."""
        mgr = object.__new__(TransferManager)
        node_mock             = MagicMock()
        node_mock.peers_lock  = __import__("threading").RLock()
        node_mock.peer_sessions = {}
        node_mock.identity_manager.get_peer_id.return_value = "local_peer_id"
        mgr._node         = node_mock
        ctrl_mock         = MagicMock()
        # Simulate peer not connected
        ctrl_mock._peer_id_to_tcp.return_value = None
        mgr._ctrl         = ctrl_mock
        mgr._schedule_gui = lambda fn: None
        mgr._transfers    = {}
        mgr._lock         = __import__("threading").Lock()
        for attr in ("on_transfer_started", "on_transfer_progress",
                     "on_transfer_complete", "on_transfer_error",
                     "on_file_meta"):
            setattr(mgr, attr, None)
        return mgr

    def test_unsupported_extension(self, tmp_path: Path) -> None:
        """send_file must reject files with extensions not in the allow-list."""
        f = tmp_path / "malware.exe"
        f.write_bytes(b"data")
        mgr = self._make_manager_with_node(tmp_path)
        ok, msg = mgr.send_file(str(f), "peer_id")
        assert ok is False
        assert "Unsupported" in msg

    def test_file_not_found(self, tmp_path: Path) -> None:  # pylint: disable=unused-argument
        mgr = self._make_manager_with_node(tmp_path)
        ok, msg = mgr.send_file(str(tmp_path / "ghost.pdf"), "peer_id")
        assert ok is False
        assert "not found" in msg

    def test_empty_file(self, tmp_path: Path) -> None:
        """send_file must reject zero-byte files."""
        f = tmp_path / "empty.pdf"
        f.write_bytes(b"")
        mgr = self._make_manager_with_node(tmp_path)
        ok, msg = mgr.send_file(str(f), "peer_id")
        assert ok is False
        assert "empty" in msg.lower()

    def test_file_too_large(self, tmp_path: Path) -> None:
        """send_file must reject files exceeding the 10 MB limit."""
        f = tmp_path / "big.pdf"
        # Create a file slightly over the 10 MB limit.
        f.write_bytes(b"x" * (FILE_MAX_SIZE + 1))
        mgr = self._make_manager_with_node(tmp_path)
        ok, msg = mgr.send_file(str(f), "peer_id")
        assert ok is False
        assert "large" in msg.lower()

    def test_peer_not_connected(self, tmp_path: Path) -> None:  # pylint: disable=unused-argument
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"valid content")
        mgr = self._make_manager_with_node(tmp_path)
        ok, msg = mgr.send_file(str(f), "disconnected_peer")
        assert ok is False
        assert "connected" in msg.lower()

    @pytest.mark.parametrize("ext", sorted(FILE_ALLOWED_EXT))
    def test_all_allowed_extensions_pass_type_check(
            self, ext: str, tmp_path: Path) -> None:
        """Every FILE_ALLOWED_EXT passes the extension gate (fails later at peer check)."""
        f = tmp_path / f"test{ext}"
        f.write_bytes(b"content")
        mgr = self._make_manager_with_node(tmp_path)
        _, msg = mgr.send_file(str(f), "peer_id")
        # Should fail on "Peer not connected", not "Unsupported file type"
        assert "Unsupported" not in msg
