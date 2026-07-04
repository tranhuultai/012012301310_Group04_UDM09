"""ChatApp: root window — owns the controller and wires all callbacks."""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
from collections import deque
from pathlib import Path
import customtkinter as ctk

from config import (
    configure_logging, WINDOW_WIDTH, WINDOW_HEIGHT, DEFAULT_LISTEN_PORT,
)
from controllers.controller import ChatController
from gui import theme as T
from gui.main_window import MainWindow
from gui.ui_state import UIState
from gui.win_compat import enable_layered_window
from trust.trust_state import TrustState

configure_logging()
logger = logging.getLogger(__name__)
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


class ChatApp(ctk.CTk):
    """Root Tk window — owns the controller and wires all GUI callbacks."""

    def __init__(self, listen_port: int = DEFAULT_LISTEN_PORT) -> None:
        super().__init__()

        self.listen_port = listen_port
        self.title(f"P2PChat  —  Port {listen_port}")
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.minsize(960, 620)
        self.configure(fg_color=T.BG_APP)
        enable_layered_window(self)

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.ui_state = UIState()
        self.selected_peer_id: str | None = None
        self._peers_update_pending = False
        self._closing = False          # guard against double-close
        self._toast_after: str | None = None   # pending after() id for toast

        # Peer IDs with an active TCP session. StatusBar has a single
        # "connected peer" segment — without tracking the full set, a second
        # peer connecting silently overwrote the first's name, and *any*
        # disconnect (even of an already-superseded peer) unconditionally
        # blanked the segment to "Not connected" even while others stayed
        # connected. See _update_connection_segment.
        self._connected_peer_ids: set[str] = set()

        # Inbound-message batching. A spamming peer can fire on_message
        # dozens of times per second; queuing one after(0, ...) per message
        # let the Tk event queue balloon and froze the GUI. Instead, messages
        # are queued here and drained in capped batches (see _drain_messages).
        # A maxlen deque auto-evicts the oldest message once the backlog hits
        # cap — same bounded-FIFO convention node.py uses for its seen-message
        # cache — and append()/popleft() are individually thread-safe in
        # CPython, so no lock or swap-the-whole-list dance is needed even
        # though multiple peer-connection threads call _on_message concurrently.
        self._msg_queue: deque[tuple[str, str, str]] = deque(maxlen=self._MAX_QUEUED_MSGS)
        self._msg_drain_pending = False

        # Per-conversation message log, keyed by peer_id.
        # Each entry is one of:
        #   ("in",      sender,  text)         — received chat message
        #   ("out",     "Me",   text)          — sent chat message
        #   ("file_in", sender,  text, tid)    — received file offer
        # "text" for file_in is "📄 filename\nsize_str" for restoration.
        self._conversations: dict[str, list] = {}
        # Unread counts per peer_id (cleared when the peer is selected).
        self._unread: dict[str, int] = {}
        # Last message preview for sidebar — (preview_text, HH:MM time).
        self._last_preview: dict[str, tuple[str, str]] = {}

        # Peer IDs for which a MISMATCH TrustDialog is already open.
        # Prevents re-opening the dialog on every 5-second discovery heartbeat.
        self._mismatch_shown: set[str] = set()

        # ── Controller ────────────────────────────────────────────────
        self.controller = ChatController(
            on_system            = self._on_system,
            on_message           = self._on_message,
            on_connected         = self._on_connected,
            on_disconnect        = self._on_disconnect,
            on_peers_update      = self._on_peers_update,
            on_peer_discovered   = self._on_peer_discovered,
            on_transfer_started  = self._on_transfer_started,
            on_transfer_progress = self._on_transfer_progress,
            on_transfer_complete = self._on_transfer_complete,
            on_file_meta         = self._on_file_meta,
            schedule_gui         = lambda fn: self.after(0, fn),
        )

        # Pending FILE_META records waiting for the user to click Download.
        # Maps transfer_id → TransferMeta
        self._pending_metas: dict = {}

        # ── Main window ───────────────────────────────────────────────
        self.main_window = MainWindow(
            self,
            on_peer_select    = self._handle_peer_selected,
            on_peer_connect   = self._handle_peer_connect,
            on_trust          = self._handle_trust,
            on_block          = self._handle_block,
            on_manual_connect = self._handle_manual_connect,
            on_broadcast      = self._handle_broadcast,
            on_disconnect     = self._handle_disconnect,
            on_add_contact    = self._handle_add_contact,
        )
        self.main_window.set_send_callback(self._handle_send_message)

        # Give the TransferPanel a reference to the controller so it can call
        # send_file / cancel_transfer directly.
        self.main_window.transfer_panel.controller = self.controller
        self.main_window.transfer_panel.on_file_sent = self._handle_file_sent

        # ── Toast overlay (created once, hidden by default) ───────────
        self._toast = ctk.CTkLabel(
            self, text="", height=32, corner_radius=10,
            fg_color=T.BG_TOAST, text_color=T.TEXT_PRI,
            font=("Segoe UI", 11), padx=16)
        # Not placed until first toast

        # ── Start node (in background thread so UI appears immediately) ─
        self._boot_ui()
        # Deferred via after(0, ...) rather than starting the thread
        # directly here: this constructor runs *before* main.py calls
        # mainloop(), so a thread started here can — if binding the socket
        # and starting discovery happens to finish first — call
        # self.after() from a background thread before mainloop() has
        # actually begun. Tk raises "main thread is not in main loop" for
        # that (a real, timing-dependent crash reproduced in practice, not
        # a hypothetical). Scheduling the thread's own start via after(0)
        # guarantees it can't even begin until the event loop is confirmed
        # running — after(0, ...) called from the main thread before
        # mainloop() starts is safe and simply fires once it does.
        self.after(0, self._launch_node_thread)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ #
    # Boot sequence                                                        #
    # ------------------------------------------------------------------ #

    def _boot_ui(self) -> None:
        """Show the 'starting…' state while the node initialises."""
        self.main_window.set_status("Starting node…", T.WARNING)

    def _launch_node_thread(self) -> None:
        """Start the node's background thread (see why this is deferred
        above, where it's scheduled)."""
        threading.Thread(target=self._start_node_bg,
                         daemon=True, name="NodeBoot").start()

    def _start_node_bg(self) -> None:
        """Start the P2P node in a background thread, then update UI."""
        username = socket.gethostname()
        ok, msg  = self.controller.start_node("0.0.0.0", self.listen_port, username)

        def _done() -> None:
            if ok:
                pid = self.controller.get_local_peer_id()
                fp  = self.controller.get_local_fingerprint()
                self.main_window.set_identity(pid[:16] + "…", fp[:23] + "…")
                self.main_window.set_status(
                    f"Online · port {self.listen_port}", T.SUCCESS)
                self.controller.discover_peers()
            else:
                self.main_window.set_status(msg, T.DANGER)
                self._toast_show(f"⚠  {msg}", T.DANGER)

        self.after(0, _done)

    # ------------------------------------------------------------------ #
    # Toast helper                                                         #
    # ------------------------------------------------------------------ #

    def _toast_show(self, text: str, color: str = T.TEXT_PRI,
                    duration_ms: int = 3000) -> None:
        """Show a transient toast at the bottom-centre for *duration_ms* ms."""
        self._toast.configure(text=text, text_color=color)
        self._toast.place(relx=0.5, rely=0.96, anchor="s")
        self._toast.lift()
        if self._toast_after:
            try:
                self.after_cancel(self._toast_after)
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        self._toast_after = self.after(duration_ms, self._toast_hide)

    def _toast_hide(self) -> None:
        self._toast.place_forget()
        self._toast_after = None

    # ------------------------------------------------------------------ #
    # Controller → GUI (always marshalled to Tk thread via after(0, …))  #
    # ------------------------------------------------------------------ #

    def _on_system(self, message: str) -> None:
        self.after(0, lambda: self.main_window.add_system_message(message))

    # Max messages rendered per drain tick. Keeps each Tk idle callback
    # short so the mainloop stays responsive even under sustained spam from
    # several peers; any excess stays queued for the next tick.
    _MSG_BATCH_SIZE = 25

    # Hard cap on the backlog itself, independent of _MSG_BATCH_SIZE — bounds
    # memory if inbound rate permanently outpaces the drain rate.
    _MAX_QUEUED_MSGS = 500

    def _on_message(self, peer_id: str, sender: str, payload: str) -> None:
        # Persist received message immediately (happens on the callback thread,
        # before marshalling to Tk — avoids losing messages if the UI thread
        # is busy). append_message does an atomic write, safe from any thread.
        #
        # Wrapped in try/except: without it, a disk error here (full disk,
        # permissions) would raise and abort this whole method *before* the
        # _msg_queue.append below ran — so the message would disappear from
        # the GUI too, not just fail to persist, and the only trace would be
        # a logged exception the user never sees.
        try:
            self.controller.message_history.append_message(peer_id, {
                "message_id": str(uuid.uuid4()),
                "peer_id":    peer_id,
                "direction":  "received",
                "content":    payload,
                "timestamp":  time.time(),
            })
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("[APP] Failed to persist message from %s: %s", peer_id[:12], exc)
            self.after(0, lambda: self._toast_show(
                "⚠  A message arrived but could not be saved to disk", T.DANGER))

        # Enqueue and let the batch drain handle rendering. `deque.append` is
        # thread-safe on its own (network thread here, Tk thread in
        # _drain_messages), and maxlen already enforces the backlog cap by
        # silently dropping the oldest entry — disk history is unaffected,
        # only how far behind the live GUI catches up is bounded.
        self._msg_queue.append((peer_id, sender, payload))
        if not self._msg_drain_pending:
            self._msg_drain_pending = True
            self.after(0, self._drain_messages)

    def _drain_messages(self) -> None:
        """Render up to _MSG_BATCH_SIZE queued messages in one Tk callback.

        Replaces the old one-after()-per-message approach, which let the Tk
        event queue balloon (and the GUI freeze/tear) whenever several peers
        spammed messages concurrently.
        """
        self._msg_drain_pending = False
        # popleft() is thread-safe on its own — no need to swap the whole
        # queue out first, unlike a plain list.
        batch = []
        for _ in range(self._MSG_BATCH_SIZE):
            try:
                batch.append(self._msg_queue.popleft())
            except IndexError:
                break
        if not batch:
            return

        now_str = time.strftime("%H:%M")   # batch drains back-to-back; one timestamp for all
        unread_peers: dict[str, tuple[str, str]] = {}   # peer_id -> (sender, last payload)
        for peer_id, sender, payload in batch:
            self._conversations.setdefault(peer_id, []).append(("in", sender, payload))
            self._last_preview[peer_id] = (
                payload[:40] + "…" if len(payload) > 40 else payload,
                now_str,
            )
            if peer_id == self.selected_peer_id:
                self.main_window.add_received_message(sender, payload)
            else:
                self._unread[peer_id] = self._unread.get(peer_id, 0) + 1
                unread_peers[peer_id] = (sender, payload)

        if unread_peers:
            self._apply_unread_badges()
            # One toast summarising the batch, not one per message — avoids
            # toast spam (and the after()-cancel churn from _toast_show).
            if len(unread_peers) == 1:
                (sender, payload), = unread_peers.values()
                preview = payload[:38] + "…" if len(payload) > 38 else payload
                self._toast_show(f"💬  {sender}: {preview}")
            else:
                self._toast_show(f"💬  New messages from {len(unread_peers)} peers")

        self._schedule_peers_redraw()

        # More arrived (or was left over) — keep draining without waiting
        # for a fresh message to re-trigger the loop.
        if self._msg_queue and not self._msg_drain_pending:
            self._msg_drain_pending = True
            self.after(0, self._drain_messages)

    def _on_connected(self, peer_id: str, tcp_addr: str) -> None:
        def _upd() -> None:
            self.ui_state.set_connection_status("connected")
            # Use peer_id directly — no fragile tcp_address search loop.
            info = self.ui_state.discovered_peers.get(peer_id, {})
            info["connected"]   = True
            info["status"]      = "connected"
            # Sync the live (possibly ephemeral) tcp_address so that
            # _on_disconnect can find this peer by tcp_addr in ui_state.
            info["tcp_address"] = tcp_addr
            self.ui_state.discovered_peers[peer_id] = info
            peer_name = info.get("username", tcp_addr)

            self._connected_peer_ids.add(peer_id)
            self._update_connection_segment()
            self.main_window.set_status(f"Connected: {peer_name}", T.ACCENT)
            self._toast_show(f"🔗 Connected to {peer_name}", T.SUCCESS)

            # If this peer is currently selected, refresh header + details.
            if peer_id == self.selected_peer_id:
                self.main_window.set_active_chat(
                    username    = peer_name,
                    status      = "connected",
                    trust_state = info.get("trust_state", TrustState.NEW))
                self.main_window.update_peer_details(info)
                self.main_window.add_system_message(
                    "🔗 Secure session started · End-to-End Encrypted")
            self._schedule_peers_redraw()
        self.after(0, _upd)

    def _on_disconnect(self, tcp_addr: str) -> None:
        def _upd() -> None:
            self.ui_state.set_connection_status("offline")
            peer_name = tcp_addr

            # Resolve peer_id from controller mapping (registered at connect
            # time) — more reliable than searching by tcp_address, which may
            # not yet be in ui_state if the peer never sent a discovery packet.
            resolved_pid = self.controller.get_peer_id_for_tcp(tcp_addr)
            found_pid: str | None = None

            for pid, info in self.ui_state.discovered_peers.items():
                if pid == resolved_pid or info.get("tcp_address") == tcp_addr:
                    info["connected"] = False
                    info["status"]    = "online"
                    peer_name = info.get("username", tcp_addr)
                    self.ui_state.discovered_peers[pid] = info
                    found_pid = pid
                    break
            # Update header if the disconnected peer is currently selected.
            # resolved_pid may be None (mapping is cleared before this closure
            # runs on the Tk thread), so fall back to the pid found in the loop.
            effective_pid = resolved_pid or found_pid
            # Only this peer leaves the connected set — other active
            # sessions must keep showing in the status bar.
            if effective_pid:
                self._connected_peer_ids.discard(effective_pid)
            self._update_connection_segment()
            self.main_window.set_status(
                f"Disconnected: {peer_name}", T.WARNING)
            self._toast_show(f"🔌 {peer_name} disconnected", T.WARNING)
            if self.selected_peer_id and self.selected_peer_id == effective_pid:
                sel = self.ui_state.discovered_peers.get(
                    self.selected_peer_id, {})
                self.main_window.set_active_chat(
                    username    = sel.get("username", "?"),
                    status      = "online",
                    trust_state = sel.get("trust_state", TrustState.NEW))
                self.main_window.update_peer_details(sel)
                self.main_window.add_system_message("🔌 Session ended")
            # If no peer is currently selected, restore the welcome placeholder.
            elif self.selected_peer_id is None:
                self.main_window.show_empty_state()
            self._schedule_peers_redraw()
        self.after(0, _upd)

    # ── File transfer callbacks ───────────────────────────────────────────

    def _on_transfer_started(self, tid: str, filename: str,
                             peer_name: str, direction: str) -> None:
        """Show a new transfer card in the TransferPanel."""
        self.main_window.on_transfer_started(tid, filename, peer_name, direction)

    def _on_transfer_progress(self, tid: str, fraction: float) -> None:
        """Update the progress bar for *tid*."""
        self.main_window.on_transfer_progress(tid, fraction)

    def _on_transfer_complete(self, tid: str, success: bool, message: str) -> None:
        """Mark the transfer card as done or failed, then remove it after a delay."""
        if success:
            short = message
            if "Saved to " in message:
                short = "✓  Saved to downloads/" + os.path.basename(
                    message.replace("Saved to ", ""))
            elif "Sent " in message:
                short = "✓  " + message
            self._toast_show(short, T.SUCCESS)
        else:
            # Shorten long error messages for the toast
            short_err = message if len(message) < 80 else message[:77] + "…"
            self._toast_show(f"✗  {short_err}", T.DANGER)
        # Release the pending meta — transfer is over, no more downloads.
        self._pending_metas.pop(tid, None)
        # Remove card after 3 s so the user can read the result.
        self.after(3000, lambda: self.main_window.on_transfer_complete(tid))

    def _on_file_meta(self, meta, peer_name: str) -> None:
        """Show a Download button in the chat for an incoming file offer.

        The message appears inside the conversation the user is viewing;
        if the sender is a different peer, the sender's conversation gets
        a notification when they switch to it.
        """
        tid       = meta.transfer_id
        filename  = meta.filename
        filesize  = meta.filesize
        sender_id = meta.sender_id

        # Store pending meta for when the user clicks Download.
        self._pending_metas[tid] = meta

        # Format readable size
        if filesize < 1024:
            size_str = f"{filesize} B"
        elif filesize < 1024 * 1024:
            size_str = f"{filesize / 1024:.1f} KB"
        else:
            size_str = f"{filesize / (1024*1024):.1f} MB"

        # File message text shown in chat
        file_msg = f"📄 {filename}\n{size_str}"

        # Store in conversation log as a special file entry
        self._conversations.setdefault(sender_id, []).append(
            ("file_in", peer_name, file_msg, tid))

        # If sender is currently selected, render in chat immediately.
        if sender_id == self.selected_peer_id:
            self.main_window.add_file_message(
                sender=peer_name,
                filename=filename,
                size_str=size_str,
                on_download=lambda t=tid: self._handle_download(t),
            )
        else:
            self._unread[sender_id] = self._unread.get(sender_id, 0) + 1
            self._toast_show(f"📄 {peer_name}: {filename}", T.ACCENT)
            self._apply_unread_badges()

    def _handle_file_sent(self, filepath: str, peer_id: str) -> None:
        """Called by TransferPanel after FILE_META is sent — show bubble in sender's chat."""
        path = Path(filepath)
        size = path.stat().st_size if path.exists() else 0
        if size < 1024:
            size_str = f"{size} B"
        elif size < 1024 * 1024:
            size_str = f"{size / 1024:.1f} KB"
        else:
            size_str = f"{size / (1024 * 1024):.1f} MB"

        file_msg = f"📄 {path.name}\n{size_str}"
        self._conversations.setdefault(peer_id, []).append(("file_out", "Me", file_msg))
        if peer_id == self.selected_peer_id:
            self.main_window.add_file_sent_message("Me", path.name, size_str)

    def _handle_download(self, transfer_id: str) -> None:
        """Called when the user clicks Download in a file message."""
        ok = self.controller.request_download(transfer_id)
        if not ok:
            self._toast_show("⚠  Cannot start download — peer may have disconnected.",
                             T.DANGER)

    def _on_peers_update(self) -> None:
        self.after(0, self._refresh_stats)

    def _on_peer_discovered(self, peer_id: str, info: dict) -> None:
        def _upd() -> None:
            self.ui_state.update_discovered_peer(peer_id, info)
            status      = info.get("status", "online")
            trust_state = info.get("trust_state", TrustState.NEW)

            if status == "online":
                self.main_window.status_bar.set_discovery(True)

            # When a known peer's fingerprint has changed, show a security
            # warning dialog exactly once per session.  The TOFU engine has
            # already set trust_state=MISMATCH; the user must decide whether
            # to accept the new key or block the peer.
            if (trust_state == TrustState.MISMATCH
                    and peer_id not in self._mismatch_shown
                    and status != "offline"):
                self._mismatch_shown.add(peer_id)
                self._show_mismatch_dialog(peer_id, info)

            if peer_id == self.selected_peer_id:
                self.main_window.update_peer_details(info)
                self.main_window.set_active_chat(
                    username    = info.get("username", peer_id),
                    status      = status,
                    trust_state = trust_state)
            self._schedule_peers_redraw()
        self.after(0, _upd)

    def _show_mismatch_dialog(self, peer_id: str, info: dict) -> None:
        """Show a security warning when peer_id's fingerprint has changed,
        letting the user accept the new key or block the peer."""
        rec = self.controller.node.tofu.store.get_peer(peer_id) if self.controller.node else None
        known_fp   = rec.get("fingerprint", "—") if rec else "—"
        current_fp = info.get("fingerprint", "—")
        username   = info.get("username", peer_id[:8])

        peer_info_for_dialog = {
            "username":            username,
            "peer_id":             peer_id,
            "known_fingerprint":   known_fp,
            "current_fingerprint": current_fp,
        }

        def _on_decision(result: str) -> None:
            self._mismatch_shown.discard(peer_id)
            if result == "update":
                # Accept new key: update trust store and re-trust the peer.
                if self.controller.node:
                    self.controller.node.tofu.accept_mismatch(peer_id, current_fp)
                self._refresh_peer_info(peer_id)
                self._toast_show(f"✓  New key accepted for {username}", T.SUCCESS)
            elif result == "block":
                self.controller.block_peer(peer_id)
                self._refresh_peer_info(peer_id)
                self._toast_show(f"⊘  {username} blocked", T.DANGER)
            else:
                # "skip" — user dismissed. Don't re-show until next session.
                pass

        self.main_window.show_trust_dialog(
            mode      = "warning",
            peer_info = peer_info_for_dialog,
            callback  = _on_decision,
        )
        logger.info("[APP] MISMATCH dialog shown for %s", peer_id[:12])

    # ------------------------------------------------------------------ #
    # Throttled sidebar rebuild                                            #
    # ------------------------------------------------------------------ #

    def _schedule_peers_redraw(self) -> None:
        """Coalesce sidebar rebuilds to at most one per 300 ms."""
        if not self._peers_update_pending:
            self._peers_update_pending = True
            self.after(300, self._do_peers_redraw)

    def _do_peers_redraw(self) -> None:
        self._peers_update_pending = False
        # Inject last-message preview so sidebar cards can show it.
        for pid, info in self.ui_state.discovered_peers.items():
            preview_data = self._last_preview.get(pid)
            if preview_data:
                info["last_message"], info["last_message_time"] = preview_data
        self.main_window.update_discovered_peers(self.ui_state.discovered_peers)
        self._refresh_stats()

    # ------------------------------------------------------------------ #
    # GUI → Controller                                                     #
    # ------------------------------------------------------------------ #

    def _handle_peer_selected(self, peer_id: str, peer_info: dict) -> None:
        self.selected_peer_id = peer_id
        self.controller.set_current_peer(peer_id)
        self.ui_state.select_peer(peer_id)

        # Clear unread badge for this peer.
        if self._unread.pop(peer_id, 0):
            self._apply_unread_badges()

        self.main_window.update_peer_details(peer_info)
        self.main_window.set_active_chat(
            username    = peer_info.get("username", peer_id),
            status      = peer_info.get("status", "offline"),
            trust_state = peer_info.get("trust_state", TrustState.NEW))

        # Reload this peer's conversation into the chat area.
        # Always load text messages from disk (authoritative source) — this
        # fixes the race where _conversations[peer_id] was seeded by an
        # incoming message before the user clicked the peer, causing the full
        # disk history to be skipped.
        self.main_window.clear_chat()
        uname = peer_info.get("username", peer_id[:8])
        disk_records = self.controller.message_history.load_history(peer_id)
        disk_entries: list = []
        for rec in disk_records:
            if rec.get("direction") == "sent":
                disk_entries.append(("out", "Me", rec.get("content", "")))
            else:
                disk_entries.append(("in", uname, rec.get("content", "")))

        # Preserve current-session file entries (file_in / file_out) — these
        # are never written to disk, so they come only from this session.
        session_files = [
            e for e in self._conversations.get(peer_id, [])
            if e[0] in ("file_in", "file_out")
        ]
        self._conversations[peer_id] = disk_entries + session_files

        for entry in self._conversations[peer_id]:
            direction = entry[0]
            sender    = entry[1]
            msg       = entry[2]
            if direction == "out":
                self.main_window.add_sent_message(
                    "Me", peer_info.get("username", peer_id), msg)
            elif direction == "file_in":
                tid = entry[3] if len(entry) > 3 else None
                lines    = msg.split("\n", 1)
                filename = lines[0].replace("📄 ", "").strip()
                size_str = lines[1] if len(lines) > 1 else ""
                cb = (lambda t=tid: self._handle_download(t)) if tid else None
                self.main_window.add_file_message(
                    sender=sender, filename=filename,
                    size_str=size_str, on_download=cb)
            elif direction == "file_out":
                lines    = msg.split("\n", 1)
                filename = lines[0].replace("📄 ", "").strip()
                size_str = lines[1] if len(lines) > 1 else ""
                self.main_window.add_file_sent_message("Me", filename, size_str)
            else:
                self.main_window.add_received_message(sender, msg)

        # If no history and peer is not yet connected, show a hint.
        if not self._conversations.get(peer_id):
            if not peer_info.get("connected"):
                self.main_window.add_system_message(
                    "Click Connect to start an encrypted session")

        self.main_window.focus_message_box()

    def _apply_unread_badges(self) -> None:
        """Push unread counts into discovered_peers so the sidebar shows them."""
        for pid, info in self.ui_state.discovered_peers.items():
            info["unread"] = self._unread.get(pid, 0)

    def _handle_peer_connect(self, peer_id: str, peer_info: dict) -> None:
        # Always use the freshest peer_info from ui_state — the sidebar card
        # may hold a stale dict created before the discovery heartbeat updated
        # the IP or tcp_port fields.
        fresh = self.ui_state.discovered_peers.get(peer_id) or peer_info
        ip    = fresh.get("ip")
        port  = fresh.get("port")
        if not ip:
            self._toast_show("⚠  Peer IP unknown — wait for discovery.", T.DANGER)
            return
        if not port:
            self._toast_show("⚠  Peer port unknown — wait for discovery.", T.DANGER)
            return
        self.main_window.set_status(f"Connecting to {ip}:{port}…", T.WARNING)
        # Run connect in background so UI doesn't freeze on timeout
        def _connect() -> None:
            ok = self.controller.connect_to_peer(ip, int(port))
            if not ok:
                self.after(0, lambda: (
                    self.main_window.set_status(
                        f"Connection failed: {ip}:{port}", T.DANGER),
                    self._toast_show(f"⚠  Could not reach {ip}:{port}", T.DANGER),
                ))
        threading.Thread(target=_connect, daemon=True, name="Connect").start()

    def _handle_disconnect(self, peer_id: str) -> None:
        """Close the active session with *peer_id* (user pressed Disconnect)."""
        info = self.ui_state.discovered_peers.get(peer_id, {})
        name = info.get("username", peer_id[:8])
        ok = self.controller.disconnect_peer(peer_id)
        if ok:
            self._toast_show(f"🔌 Disconnected from {name}", T.WARNING)
        else:
            self._toast_show("Not connected.", T.TEXT_MUTED)
        # The node fires on_disconnect which will refresh the UI.

    def _handle_add_contact(self, peer_id: str) -> None:
        """Save *peer_id* to the contact book with their current identity info."""
        info = self.ui_state.discovered_peers.get(peer_id, {})
        alias       = info.get("username", peer_id[:8])
        trust_state = info.get("trust_state", "NEW")
        fingerprint = info.get("fingerprint", "")

        self.controller.contact_book.add_contact(
            peer_id     = peer_id,
            alias       = alias,
            trust_state = trust_state,
            fingerprint = fingerprint,
        )
        self._toast_show(f"⭐  {alias} added to contacts", T.SUCCESS)
        self._refresh_stats()
        logger.info("[APP] Contact added: %s (%s)", peer_id[:12], alias)

    def _handle_trust(self, peer_id: str) -> None:
        """Trust or Unblock the peer, depending on current state."""
        info = self.ui_state.discovered_peers.get(peer_id, {})
        if info.get("trust_state") == "BLOCKED":
            # "Trust / Verify" button acts as Unblock when peer is blocked.
            self.controller.unblock_peer(peer_id)
            self._toast_show("↩  Peer unblocked", T.SUCCESS)
        else:
            self.controller.trust_peer(peer_id)
            self._toast_show("✓  Peer trusted", T.SUCCESS)
        self._refresh_peer_info(peer_id)

    def _handle_block(self, peer_id: str) -> None:
        """Block the peer and immediately disconnect them."""
        self.controller.block_peer(peer_id)
        self._refresh_peer_info(peer_id)
        self._toast_show("⊘  Peer blocked — connection closed", T.DANGER)
        # If we were chatting with this peer, update the header.
        if peer_id == self.selected_peer_id:
            info = self.ui_state.discovered_peers.get(peer_id, {})
            self.main_window.set_active_chat(
                username    = info.get("username", peer_id),
                status      = "offline",
                trust_state = "BLOCKED")

    def _handle_manual_connect(self, ip: str, port_str: str) -> None:
        from network.validation import validate_ip, validate_port  # pylint: disable=import-outside-toplevel
        if not validate_ip(ip):
            self._toast_show("⚠  Invalid IP address.", T.DANGER)
            return
        if not validate_port(port_str):
            self._toast_show("⚠  Invalid port number.", T.DANGER)
            return
        self.main_window.set_status(f"Connecting to {ip}:{port_str}…", T.WARNING)
        def _connect() -> None:
            ok = self.controller.connect_to_peer(ip, int(port_str))
            if not ok:
                self.after(0, lambda: self._toast_show(
                    f"⚠  Could not reach {ip}:{port_str}", T.DANGER))
        threading.Thread(target=_connect, daemon=True, name="ManualConnect").start()

    def _handle_broadcast(self) -> None:
        msg = self.main_window.get_message_text().strip()
        if not msg:
            return
        sent, failed = self.controller.broadcast_message(msg)
        if sent:
            self.main_window.add_sent_message("Me", "Everyone", msg)
            self.main_window.clear_message_text()
            self._toast_show(f"📢 Sent to {sent} peer(s)", T.SUCCESS)
        if failed:
            self._toast_show(f"⚠  Failed for {failed} peer(s)", T.WARNING)

    def _handle_send_message(self) -> None:
        if self.selected_peer_id is None:
            self._toast_show("Select a peer first.", T.WARNING)
            return
        msg = self.main_window.get_message_text().strip()
        if not msg:
            return

        # Guard: don't allow sending to a BLOCKED peer.
        sel = self.ui_state.discovered_peers.get(self.selected_peer_id, {})
        if sel.get("trust_state") == TrustState.BLOCKED:
            self._toast_show("⊘  Peer is blocked — unblock to chat.", T.DANGER)
            return

        ok = self.controller.send_message(msg, self.selected_peer_id)
        if ok:
            # Store in conversation log + render.
            self._conversations.setdefault(self.selected_peer_id, []).append(
                ("out", "Me", msg))
            self._last_preview[self.selected_peer_id] = (
                f"You: {msg[:36]}…" if len(msg) > 36 else f"You: {msg}",
                time.strftime("%H:%M"),
            )
            self.main_window.add_sent_message(
                "Me", sel.get("username", self.selected_peer_id[:8]), msg)
            self.main_window.clear_message_text()
            self._schedule_peers_redraw()
        else:
            # node.send_message() returns False for more than one reason
            # (peer not active, or the message exceeded MAX_PACKET_SIZE —
            # see network/node.py) and doesn't currently report which, so
            # the toast stays deliberately non-specific rather than assert
            # a cause we haven't actually confirmed.
            self._toast_show(
                "⚠  Message not sent — check connection, or message may be too long.",
                T.DANGER,
            )

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _update_connection_segment(self) -> None:
        """Reflect the current set of connected peers in the status bar.

        StatusBar only has one "connected peer" slot, so with 2+ peers
        connected at once we can't show every name — fall back to a count.
        Called after _connected_peer_ids changes (connect/disconnect).
        """
        n = len(self._connected_peer_ids)
        if n == 0:
            self.main_window.status_bar.set_disconnected()
        elif n == 1:
            (pid,) = self._connected_peer_ids
            name = self.ui_state.discovered_peers.get(pid, {}).get("username", pid[:8])
            self.main_window.status_bar.set_connected_peer(name)
        else:
            self.main_window.status_bar.set_connected_peer(f"{n} peers")

    def _refresh_peer_info(self, peer_id: str) -> None:
        info = self.ui_state.discovered_peers.get(peer_id)
        if info is None:
            return
        info["trust_state"] = self.controller.get_trust_state(peer_id)
        self.ui_state.discovered_peers[peer_id] = info
        self._schedule_peers_redraw()
        if peer_id == self.selected_peer_id:
            self.main_window.update_peer_details(info)
            self.main_window.set_active_chat(
                username    = info.get("username", peer_id),
                status      = info.get("status", "online"),
                trust_state = info.get("trust_state", TrustState.NEW))

    def _refresh_stats(self) -> None:
        peers     = len(self.ui_state.discovered_peers)
        connected = sum(
            1 for i in self.ui_state.discovered_peers.values()
            if i.get("connected") or i.get("status") == "connected")
        self.main_window.update_stats(
            peers     = peers,
            connected = connected,
            contacts  = len(self.controller.contact_book.get_all_contacts()))

    # ------------------------------------------------------------------ #
    # Graceful shutdown                                                    #
    # ------------------------------------------------------------------ #

    def _on_close(self) -> None:
        """Gracefully stop networking then destroy the window."""
        if self._closing:
            return
        self._closing = True

        self.main_window.set_status("Closing…", T.WARNING)
        self.update_idletasks()   # flush any pending redraws

        def _shutdown() -> None:
            try:
                self.controller.stop()
            finally:
                self.after(0, self.destroy)

        threading.Thread(target=_shutdown, daemon=True, name="Shutdown").start()
