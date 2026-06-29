"""File transfer manager for P2PChat.

Workflow (Telegram-style — no Accept/Reject dialog):

    Sender:
        1. User picks a file → send_file() called.
        2. FILE_META sent → metadata appears in receiver's chat.
        3. Sender waits for DOWNLOAD_REQUEST.

    Receiver:
        4. GUI shows "📄 filename  ⬇ Download" button in chat.
        5. User clicks Download → DOWNLOAD_REQUEST sent.

    Sender:
        6. Receives DOWNLOAD_REQUEST → sends FILE_START, then N × FILE_CHUNK.
        7. Sends FILE_COMPLETE with SHA-256.

    Receiver:
        8. Each FILE_CHUNK: Base64-decode → Fernet-decrypt → append to .part.
        9. On FILE_COMPLETE: verify SHA-256. Rename .part → final file.

Encoding strategy:
    Raw bytes → Fernet.encrypt() → Base64.b64encode() → UTF-8 str in JSON.
    Reason: JSON cannot carry raw bytes; Base64 is an encoding, NOT encryption.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from config import FILE_CHUNK_SIZE, FILE_MAX_SIZE, FILE_ALLOWED_EXT, FILE_DOWNLOAD_DIR
from message.protocol import PacketType
from security.crypto import CryptoHandler

logger = logging.getLogger(__name__)

# ── Transfer state constants ────────────────────────────────────────────────
_ST_PENDING   = "pending"    # META sent; waiting for DOWNLOAD_REQUEST
_ST_SENDING   = "sending"    # actively sending chunks
_ST_RECEIVING = "receiving"  # actively receiving chunks
_ST_DONE      = "done"
_ST_CANCELLED = "cancelled"
_ST_ERROR     = "error"


class TransferMeta:
    """Immutable record describing a file transfer (DTO — frozen after creation).

    Attributes:
        transfer_id: UUID4 string identifying this transfer.
        filename: Base filename (no path) of the file.
        filesize: Total size in bytes.
        mime_type: MIME type string (e.g. "application/pdf").
        sha256: Hex digest of the complete file contents.
        sender_id: SHA-256 peer_id of the sender.
        receiver_id: SHA-256 peer_id of the receiver.
        timestamp: Unix timestamp when the transfer was created.
    """

    __slots__ = (
        "transfer_id", "filename", "filesize", "mime_type",
        "sha256", "sender_id", "receiver_id", "timestamp",
    )

    def __init__(self, transfer_id: str, filename: str, filesize: int,
                 mime_type: str, sha256: str, sender_id: str,
                 receiver_id: str, timestamp: float) -> None:
        self.transfer_id = transfer_id
        self.filename    = filename
        self.filesize    = filesize
        self.mime_type   = mime_type
        self.sha256      = sha256
        self.sender_id   = sender_id
        self.receiver_id = receiver_id
        self.timestamp   = timestamp

    def to_dict(self) -> dict:
        """Serialise to a plain dict for JSON transport."""
        return {slot: getattr(self, slot) for slot in self.__slots__}

    @classmethod
    def from_dict(cls, d: dict) -> "TransferMeta":
        """Deserialise from a packet field dict."""
        return cls(
            transfer_id = d["transfer_id"],
            filename    = d["filename"],
            filesize    = int(d["filesize"]),
            mime_type   = d.get("mime_type", "application/octet-stream"),
            sha256      = d["sha256"],
            sender_id   = d.get("sender_id", ""),
            receiver_id = d.get("receiver_id", ""),
            timestamp   = float(d.get("timestamp", time.time())),
        )


def _sha256_file(path: Path) -> str:
    """Return the hex SHA-256 digest of the file at *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _mime_for_ext(ext: str) -> str:
    """Return a simple MIME type string for *ext* (lower-case with dot)."""
    return {
        ".pdf":  "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".txt":  "text/plain",
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".zip":  "application/zip",
    }.get(ext, "application/octet-stream")


class TransferManager:
    """Coordinates all active file transfers for one P2PNode.

    Responsibilities
    ----------------
    * Validate files before sending (size, extension).
    * Build and dispatch FILE_META / FILE_START / FILE_CHUNK / FILE_COMPLETE.
    * Receive and dispatch DOWNLOAD_REQUEST / FILE_CHUNK / FILE_COMPLETE.
    * Write received chunks to .part files; rename on integrity check.
    * Report progress to the GUI through lightweight callbacks.
    * Never block the GUI thread or the network receive thread.

    Thread model
    ------------
    Each outbound transfer runs in its own daemon thread (_SendThread).
    The inbound chunk-write loop also runs in a daemon thread (_RecvThread).
    Both threads communicate with the GUI exclusively through the after()
    mechanism supplied by the ChatApp (via schedule_gui).
    """

    def __init__(
        self,
        node,                           # P2PNode (duck-typed to avoid circular import)
        controller,                     # ChatController
        schedule_gui: Callable,         # app.after(0, fn) equivalent
        on_transfer_started:  Optional[Callable] = None,
        on_transfer_progress: Optional[Callable] = None,
        on_transfer_complete: Optional[Callable] = None,
        on_transfer_error:    Optional[Callable] = None,
        on_file_meta:         Optional[Callable] = None,
    ) -> None:
        """Initialise and wire the node's on_file_packet hook.

        Args:
            node: The running P2PNode instance.
            controller: ChatController (for tcp address lookup).
            schedule_gui: A fn → None that schedules *fn* on the Tk thread.
            on_transfer_started: (tid, filename, peer_name, direction)
            on_transfer_progress: (tid, fraction) — fraction in [0, 1].
            on_transfer_complete: (tid, success, message)
            on_transfer_error: (tid, message)
            on_file_meta: (meta: TransferMeta, peer_name: str) — called when
                the receiver gets a FILE_META and should show a Download button.
        """
        self._node         = node
        self._ctrl         = controller
        self._schedule_gui = schedule_gui

        self.on_transfer_started  = on_transfer_started
        self.on_transfer_progress = on_transfer_progress
        self.on_transfer_complete = on_transfer_complete
        self.on_transfer_error    = on_transfer_error
        self.on_file_meta         = on_file_meta

        # transfer_id → state dict
        self._transfers: dict[str, dict] = {}
        self._lock = threading.Lock()

        # Hook into the node
        node.on_file_packet = self._on_file_packet

        # Ensure download directory exists
        Path(FILE_DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Public API — called by controller / GUI                             #
    # ------------------------------------------------------------------ #

    def send_file(self, filepath: str, peer_id: str) -> tuple[bool, str]:
        """Validate *filepath* and send FILE_META to *peer_id*.

        Args:
            filepath: Absolute or relative path to the file to send.
            peer_id: SHA-256 identifier of the destination peer.

        Returns:
            (True, transfer_id) if meta was sent, (False, error_msg)
            if validation failed or peer is not connected.
        """
        path = Path(filepath)
        ext  = path.suffix.lower()

        if ext not in FILE_ALLOWED_EXT:
            return False, f"Unsupported file type: {ext}"

        if not path.exists():
            return False, "File not found."

        size = path.stat().st_size
        if size == 0:
            return False, "File is empty."
        if size > FILE_MAX_SIZE:
            mb = size / (1024 * 1024)
            return False, f"File too large: {mb:.1f} MB (limit 10 MB)."

        tcp_addr = self._ctrl._peer_id_to_tcp(peer_id)  # pylint: disable=protected-access
        if tcp_addr is None:
            return False, "Peer is not connected."

        tid   = str(uuid.uuid4())
        sha   = _sha256_file(path)
        meta  = TransferMeta(
            transfer_id = tid,
            filename    = path.name,
            filesize    = size,
            mime_type   = _mime_for_ext(ext),
            sha256      = sha,
            sender_id   = self._node.identity_manager.get_peer_id(),
            receiver_id = peer_id,
            timestamp   = time.time(),
        )

        with self._lock:
            self._transfers[tid] = {
                "state":    _ST_PENDING,
                "meta":     meta,
                "filepath": path,
                "tcp_addr": tcp_addr,
                "peer_id":  peer_id,
            }

        # Send FILE_META over the existing session (no file data yet).
        packet = {"type": PacketType.FILE_META, "meta": meta.to_dict()}
        if not self._node.send_raw_packet(packet, tcp_addr):
            with self._lock:
                self._transfers.pop(tid, None)
            return False, "Failed to send file metadata."

        logger.info("[TRANSFER] FILE_META sent tid=%s file=%s", tid[:8], path.name)
        return True, tid

    def cancel_transfer(self, transfer_id: str) -> None:
        """Cancel an in-progress transfer (sender or receiver side).

        Args:
            transfer_id: The transfer to cancel.
        """
        with self._lock:
            entry = self._transfers.get(transfer_id)
        if entry is None:
            return

        tcp_addr = entry["tcp_addr"]
        pkt = {"type": PacketType.FILE_CANCEL, "transfer_id": transfer_id}
        self._node.send_raw_packet(pkt, tcp_addr)
        self._mark(transfer_id, _ST_CANCELLED)
        logger.info("[TRANSFER] Cancelled tid=%s", transfer_id[:8])

    def request_download(self, transfer_id: str) -> bool:
        """Send DOWNLOAD_REQUEST for *transfer_id* (receiver side).

        Called when the user clicks the Download button.

        Args:
            transfer_id: The transfer to start downloading.

        Returns:
            True if the request was sent.
        """
        with self._lock:
            entry = self._transfers.get(transfer_id)
        if entry is None:
            return False

        pkt = {"type": PacketType.DOWNLOAD_REQUEST, "transfer_id": transfer_id}
        ok  = self._node.send_raw_packet(pkt, entry["tcp_addr"])
        if ok:
            self._mark(transfer_id, _ST_RECEIVING)
        return ok

    # ------------------------------------------------------------------ #
    # Inbound packet dispatcher (runs on network receive thread)          #
    # ------------------------------------------------------------------ #

    def _on_file_packet(self, packet: dict, crypto: Optional[CryptoHandler],
                        sender_pid: str) -> None:
        """Dispatch an inbound file-transfer packet to the correct handler.

        This is called by P2PNode on the receive thread — must return quickly.

        Args:
            packet: Parsed packet dict from the network.
            crypto: Active Fernet session or None.
            sender_pid: SHA-256 peer_id of the sender.
        """
        ptype = packet.get("type")
        try:
            if   ptype == PacketType.FILE_META:
                self._recv_meta(packet, sender_pid)
            elif ptype == PacketType.DOWNLOAD_REQUEST:
                self._recv_download_request(packet)
            elif ptype == PacketType.FILE_START:
                self._recv_file_start(packet)
            elif ptype == PacketType.FILE_CHUNK:
                self._recv_chunk(packet, crypto)
            elif ptype == PacketType.FILE_COMPLETE:
                self._recv_complete(packet)
            elif ptype == PacketType.FILE_CANCEL:
                self._recv_cancel(packet)
            elif ptype == PacketType.FILE_ERROR:
                self._recv_error(packet)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("[TRANSFER] Unhandled error in _on_file_packet")

    # ------------------------------------------------------------------ #
    # Receiver-side packet handlers                                        #
    # ------------------------------------------------------------------ #

    def _recv_meta(self, packet: dict, sender_pid: str) -> None:
        """Handle FILE_META — register transfer, notify GUI to show Download btn."""
        raw = packet.get("meta", {})
        try:
            meta = TransferMeta.from_dict(raw)
        except (KeyError, ValueError) as exc:
            logger.warning("[TRANSFER] Bad FILE_META: %s", exc)
            return

        tid      = meta.transfer_id
        tcp_addr = self._ctrl._peer_id_to_tcp(sender_pid)  # pylint: disable=protected-access
        if tcp_addr is None:
            logger.warning("[TRANSFER] Cannot resolve tcp for sender %s", sender_pid[:12])
            return

        # Determine a safe save path.
        save_path = self._safe_save_path(meta.filename)

        with self._lock:
            self._transfers[tid] = {
                "state":     _ST_PENDING,
                "meta":      meta,
                "save_path": save_path,
                "part_path": Path(str(save_path) + ".part"),
                "tcp_addr":  tcp_addr,
                "peer_id":   sender_pid,
                "received":  0,
                "chunks":    [],   # buffer for out-of-order (not needed with TCP, kept for clarity)
            }

        # Get peer display name for GUI
        info      = self._ctrl.get_peer_info(sender_pid) or {}
        peer_name = info.get("username", sender_pid[:8])

        def _gui() -> None:
            if self.on_file_meta:
                self.on_file_meta(meta, peer_name)
        self._schedule_gui(_gui)
        logger.info("[TRANSFER] FILE_META received tid=%s file=%s from=%s",
                    tid[:8], meta.filename, peer_name)

    def _recv_download_request(self, packet: dict) -> None:
        """Handle DOWNLOAD_REQUEST — start a sender thread."""
        tid = packet.get("transfer_id")
        if not tid:
            return
        with self._lock:
            entry = self._transfers.get(tid)
        if entry is None or entry["state"] != _ST_PENDING:
            return
        self._mark(tid, _ST_SENDING)

        t = threading.Thread(
            target=self._send_file_thread,
            args=(tid,),
            daemon=True,
            name=f"SendFile-{tid[:8]}",
        )
        t.start()

    def _recv_file_start(self, packet: dict) -> None:
        """Handle FILE_START — open the .part file for writing."""
        tid = packet.get("transfer_id")
        if not tid:
            return
        with self._lock:
            entry = self._transfers.get(tid)
        if entry is None:
            return
        # Open/truncate the .part file
        try:
            entry["part_path"].parent.mkdir(parents=True, exist_ok=True)
            with open(entry["part_path"], "wb"):
                pass   # truncate / create
            entry["part_fh"] = open(entry["part_path"], "ab")  # pylint: disable=consider-using-with
        except OSError as exc:
            logger.error("[TRANSFER] Cannot open .part file: %s", exc)
            self._send_error(tid, entry["tcp_addr"], "Disk error opening .part file")
            self._mark(tid, _ST_ERROR)
            return

        # Notify the GUI so the TransferPanel shows a progress card for the receiver.
        meta      = entry["meta"]
        info      = self._ctrl.get_peer_info(entry["peer_id"]) or {}
        peer_name = info.get("username", entry["peer_id"][:8])
        tid_copy  = tid

        def _started() -> None:
            if self.on_transfer_started:
                self.on_transfer_started(tid_copy, meta.filename, peer_name, "in")
        self._schedule_gui(_started)

    def _recv_chunk(self, packet: dict, crypto: Optional[CryptoHandler]) -> None:
        """Handle FILE_CHUNK — decrypt, decode, write to .part file."""
        tid = packet.get("transfer_id")
        if not tid:
            return
        with self._lock:
            entry = self._transfers.get(tid)
        if entry is None or entry.get("state") not in (_ST_RECEIVING, _ST_SENDING):
            return

        b64_payload = packet.get("payload", "")
        try:
            encrypted_bytes = base64.b64decode(b64_payload)
            if crypto is not None:
                raw_bytes = crypto.fernet.decrypt(encrypted_bytes)
            else:
                raw_bytes = encrypted_bytes
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("[TRANSFER] Chunk decrypt failed tid=%s: %s", tid[:8], exc)
            self._send_error(tid, entry["tcp_addr"], "Decryption failed")
            self._cleanup_recv(tid)
            return

        fh = entry.get("part_fh")
        if fh is None:
            logger.warning("[TRANSFER] No file handle for tid=%s", tid[:8])
            return
        try:
            fh.write(raw_bytes)
            fh.flush()
        except OSError as exc:
            logger.error("[TRANSFER] Write failed tid=%s: %s", tid[:8], exc)
            self._send_error(tid, entry["tcp_addr"], "Disk write failed")
            self._cleanup_recv(tid)
            return

        entry["received"] += len(raw_bytes)
        meta              = entry["meta"]
        fraction          = min(1.0, entry["received"] / max(1, meta.filesize))
        tid_copy          = tid

        def _progress() -> None:
            if self.on_transfer_progress:
                self.on_transfer_progress(tid_copy, fraction)
        self._schedule_gui(_progress)

    def _recv_complete(self, packet: dict) -> None:
        """Handle FILE_COMPLETE — verify SHA-256 and rename .part to final."""
        tid = packet.get("transfer_id")
        if not tid:
            return
        with self._lock:
            entry = self._transfers.get(tid)
        if entry is None:
            return

        fh = entry.pop("part_fh", None)
        if fh:
            try:
                fh.close()
            except OSError:
                pass

        expected_sha = packet.get("sha256", "")
        part_path    = entry["part_path"]
        save_path    = entry["save_path"]
        meta         = entry["meta"]

        if not part_path.exists():
            self._finish(tid, False, "Received no data.")
            return

        actual_sha = _sha256_file(part_path)
        if actual_sha != expected_sha:
            logger.error(
                "[TRANSFER] SHA mismatch tid=%s expected=%s got=%s",
                tid[:8], expected_sha[:12], actual_sha[:12],
            )
            # Keep .part file for diagnosis
            self._finish(tid, False,
                         f"Checksum mismatch — {meta.filename}.part kept for inspection.")
            return

        # Rename to final name
        try:
            part_path.rename(save_path)
        except OSError as exc:
            logger.error("[TRANSFER] Rename failed: %s", exc)
            self._finish(tid, False, f"Could not save file: {exc}")
            return

        logger.info("[TRANSFER] Complete tid=%s → %s", tid[:8], save_path)
        self._finish(tid, True, f"Saved to {save_path}")

    def _recv_cancel(self, packet: dict) -> None:
        """Handle FILE_CANCEL from remote peer."""
        tid = packet.get("transfer_id")
        if not tid:
            return
        self._cleanup_recv(tid)
        self._finish(tid, False, "Transfer cancelled by peer.")

    def _recv_error(self, packet: dict) -> None:
        """Handle FILE_ERROR from remote peer."""
        tid = packet.get("transfer_id")
        if not tid:
            return
        msg = packet.get("message", "Unknown error from peer.")
        self._cleanup_recv(tid)
        self._finish(tid, False, msg)

    # ------------------------------------------------------------------ #
    # Sender thread                                                        #
    # ------------------------------------------------------------------ #

    def _send_file_thread(self, tid: str) -> None:
        """Worker thread: send FILE_START + N × FILE_CHUNK + FILE_COMPLETE.

        Runs entirely outside the GUI and receive threads.
        """
        with self._lock:
            entry = self._transfers.get(tid)
        if entry is None:
            return

        meta     = entry["meta"]
        filepath = entry["filepath"]
        tcp_addr = entry["tcp_addr"]
        crypto   = self._get_crypto(tcp_addr)

        info      = self._ctrl.get_peer_info(entry["peer_id"]) or {}
        peer_name = info.get("username", entry["peer_id"][:8])

        def _started() -> None:
            if self.on_transfer_started:
                self.on_transfer_started(tid, meta.filename, peer_name, "out")
        self._schedule_gui(_started)

        # ── Send FILE_START ───────────────────────────────────────────
        start_pkt = {
            "type":        PacketType.FILE_START,
            "transfer_id": tid,
            "filename":    meta.filename,
            "filesize":    meta.filesize,
            "total_chunks": self._total_chunks(meta.filesize),
        }
        if not self._node.send_raw_packet(start_pkt, tcp_addr):
            self._finish(tid, False, "Failed to send FILE_START.")
            return

        # ── Send FILE_CHUNKs ─────────────────────────────────────────
        seq    = 0
        total  = self._total_chunks(meta.filesize)
        sent   = 0

        try:
            with open(filepath, "rb") as fh:
                while True:
                    with self._lock:
                        state = self._transfers.get(tid, {}).get("state")
                    if state in (_ST_CANCELLED, _ST_ERROR):
                        return

                    raw = fh.read(FILE_CHUNK_SIZE)
                    if not raw:
                        break

                    # Fernet-encrypt → Base64-encode → put in JSON
                    if crypto is not None:
                        encrypted = crypto.fernet.encrypt(raw)
                    else:
                        encrypted = raw
                    b64 = base64.b64encode(encrypted).decode("ascii")

                    chunk_pkt = {
                        "type":         PacketType.FILE_CHUNK,
                        "transfer_id":  tid,
                        "sequence":     seq,
                        "total_chunks": total,
                        "payload":      b64,
                    }
                    if not self._node.send_raw_packet(chunk_pkt, tcp_addr):
                        self._finish(tid, False, "Connection lost during transfer.")
                        return

                    seq  += 1
                    sent += len(raw)
                    fraction = min(1.0, sent / max(1, meta.filesize))
                    tid_copy = tid

                    def _prog(f: float = fraction, t: str = tid_copy) -> None:
                        if self.on_transfer_progress:
                            self.on_transfer_progress(t, f)
                    self._schedule_gui(_prog)

        except OSError as exc:
            logger.error("[TRANSFER] Read error tid=%s: %s", tid[:8], exc)
            self._send_error(tid, tcp_addr, f"File read error: {exc}")
            self._finish(tid, False, f"Failed to read file: {exc}")
            return

        # ── Send FILE_COMPLETE ────────────────────────────────────────
        done_pkt = {
            "type":        PacketType.FILE_COMPLETE,
            "transfer_id": tid,
            "sha256":      meta.sha256,
        }
        self._node.send_raw_packet(done_pkt, tcp_addr)
        self._finish(tid, True, f"Sent {meta.filename} successfully.")
        logger.info("[TRANSFER] Send complete tid=%s file=%s", tid[:8], meta.filename)

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _get_crypto(self, tcp_addr: str) -> Optional[CryptoHandler]:
        """Return the Fernet CryptoHandler for *tcp_addr*, or None."""
        with self._node.peers_lock:
            sess = self._node.peer_sessions.get(tcp_addr)
            return sess.get("crypto") if sess else None

    def _mark(self, tid: str, state: str) -> None:
        """Update the state of a transfer under the lock."""
        with self._lock:
            if tid in self._transfers:
                self._transfers[tid]["state"] = state

    def _finish(self, tid: str, success: bool, message: str) -> None:
        """Mark transfer done and notify GUI."""
        self._mark(tid, _ST_DONE if success else _ST_ERROR)
        tid_copy = tid

        def _gui() -> None:
            if self.on_transfer_complete:
                self.on_transfer_complete(tid_copy, success, message)
        self._schedule_gui(_gui)

    def _cleanup_recv(self, tid: Optional[str]) -> None:
        """Close and optionally remove the .part file for a failed receive."""
        if tid is None:
            return
        with self._lock:
            entry = self._transfers.get(tid)
        if entry is None:
            return
        fh = entry.pop("part_fh", None)
        if fh:
            try:
                fh.close()
            except OSError:
                pass

    def _send_error(self, tid: str, tcp_addr: str, message: str) -> None:
        """Send a FILE_ERROR packet to the remote peer."""
        pkt = {
            "type":        PacketType.FILE_ERROR,
            "transfer_id": tid,
            "message":     message,
        }
        self._node.send_raw_packet(pkt, tcp_addr)

    def _safe_save_path(self, filename: str) -> Path:
        """Return a non-colliding save path under FILE_DOWNLOAD_DIR.

        If downloads/report.pdf already exists, tries
        downloads/report_1.pdf, downloads/report_2.pdf, …

        Args:
            filename: Base filename from the transfer metadata.

        Returns:
            A Path object guaranteed not to exist at call time.
        """
        base     = Path(FILE_DOWNLOAD_DIR)
        stem     = Path(filename).stem
        suffix   = Path(filename).suffix
        candidate = base / filename
        counter  = 0
        while candidate.exists():
            counter  += 1
            candidate = base / f"{stem}_{counter}{suffix}"
        return candidate

    @staticmethod
    def _total_chunks(filesize: int) -> int:
        """Return the number of FILE_CHUNK packets needed for *filesize*."""
        return max(1, (filesize + FILE_CHUNK_SIZE - 1) // FILE_CHUNK_SIZE)
