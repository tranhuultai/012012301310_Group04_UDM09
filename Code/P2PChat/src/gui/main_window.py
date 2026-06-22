import customtkinter as ctk
import threading
import time
from gui.sidebar import Sidebar
from gui.statusbar import StatusBar
from gui.trust_dialog import TrustDialog

# Set up standard dark mode interface
ctk.set_appearance_mode("dark")

class ChatApp(ctk.CTk):
    def on_transfer_started(
        self,
        transfer_id,
        filename,
        peer_name,
        direction
    ):
        self.transfer_panel.add_transfer(
            transfer_id,
            filename,
            peer_name,
            direction
        )

    def on_transfer_complete(
        self,
        transfer_id
    ):
        self.transfer_panel.remove_transfer(
        transfer_id
    )   
    def on_peer_discovered(self, peer_info):
        """Handling when a new peer is detected"""
        peer_id = peer_info.get("peer_id")
        new_fp = peer_info.get("fingerprint")

        if peer_id not in self.trusted_peers:
            # First connection
            TrustDialog(self, mode="new_peer", peer_info=peer_info, 
                        callback=lambda res: self._handle_trust_decision(res, peer_info))
        elif self.trusted_peers[peer_id] != new_fp:
            # Warning about fingerprint change
            peer_info["known_fingerprint"] = self.trusted_peers[peer_id]
            peer_info["current_fingerprint"] = new_fp
            TrustDialog(self, mode="warning", peer_info=peer_info,
                        callback=lambda res: self._handle_trust_decision(res, peer_info))

    def _handle_trust_decision(self, action, peer_info):
        if action in ["trust", "update"]:
            self.trusted_peers[peer_info["peer_id"]] = peer_info["fingerprint"]
            self.sidebar.update_peers([peer_info["username"]])

    def on_message_received(self, peer_id, username, message):
        """Save chat history and display message"""
        timestamp = time.strftime("%H:%M")
        formatted_msg = f"[{timestamp}] {username}: {message}"
        
        if peer_id not in self.chat_history:
            self.chat_history[peer_id] = []
        self.chat_history[peer_id].append(formatted_msg)
        
        self.chat_box.configure(state="normal")
        self.chat_box.insert("end", formatted_msg + "\n")
        self.chat_box.configure(state="disabled")
        self.chat_box.see("end")

    def on_transfer_progress(self, transfer_id, progress):
        """Update progress bar in real-time"""
        self.transfer_panel.update_transfer(transfer_id, progress)

    def on_file_offer(self, peer_name, filename, transfer_id):
        """Display a notification that the file has been received"""
        self.chat_box.configure(state="normal")
        self.chat_box.insert("end", f"\n--- 📂 {peer_name} muốn gửi: {filename} ---\n")
        self.chat_box.configure(state="disabled")

    def __init__(self, listen_port=12000):
        super().__init__()
        self.title(f"💬 P2P Chat v2.0 (Port: {listen_port})")
        self.geometry("900x650")
        self.minsize(700, 500)
        self.listen_port = listen_port
        self.trusted_peers = {}
        self.chat_history = {}

        # --- Set up Responsive Grid ---
        # Column 0 (Chat) will expand (weight=1), Column 1 (Sidebar) stays fixed (weight=0)
        self.grid_columnconfigure(0, weight=1)  
        self.grid_columnconfigure(1, weight=0)  
        self.grid_rowconfigure(0, weight=1)     # main content row expands
        self.grid_rowconfigure(1, weight=0)     # Status bar row stays fixed

        self._build_ui()

    def _build_ui(self):
        # ================= LEFT AREA (MAIN CHAT) =================
        self.main_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="#1e1e2e")
        self.main_frame.grid(row=0, column=0, sticky="nsew")
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(1, weight=1) # Chat box expands automatically

        # 1. Connection bar (UX Non-tech: Hides IP/Port)
        self.top_bar = ctk.CTkFrame(self.main_frame, height=50, fg_color="transparent")
        self.top_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))

        self.nick_entry = ctk.CTkEntry(self.top_bar, placeholder_text="Enter nickname...", width=150, font=("Consolas", 12))
        self.nick_entry.pack(side="left", padx=(0, 10))

        self.connect_btn = ctk.CTkButton(
            self.top_bar, text="🔗 Start Chat", 
            font=("Consolas", 12, "bold"), fg_color="#89b4fa", text_color="#11111b",
            command=self._start_connect_thread # Call the function to start connection in a new thread
        )
        self.connect_btn.pack(side="left")

        # 2. Chat box (Disabled until conected)
        self.chat_box = ctk.CTkTextbox(
            self.main_frame, state="disabled", wrap="word", 
            font=("Consolas", 13), fg_color="#181825", text_color="#cdd6f4"
        )
        self.chat_box.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)

        # 3. Message input area (Disabled by default)
        self.input_frame = ctk.CTkFrame(self.main_frame, height=50, fg_color="transparent")
        self.input_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))

        self.msg_entry = ctk.CTkEntry(self.input_frame, placeholder_text="Enter message...", state="disabled", font=("Consolas", 13))
        self.msg_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.send_btn = ctk.CTkButton(
            self.input_frame, text="▶ Send", width=80, state="disabled",
            font=("Consolas", 12, "bold"), fg_color="#a6e3a1", text_color="#11111b"
        )
        self.send_btn.pack(side="left")

        # ================= LEFT AREA (SIDEBAR) =================
        from gui.transfer_panel import TransferPanel
        self.sidebar = Sidebar(self)
        self.sidebar.grid(row=0, column=1, sticky="ns")
        self.transfer_panel = TransferPanel(
            master=self.sidebar,
            controller=self
        )

        self.transfer_panel.pack(
            fill="both",
            expand=True,
            padx=5,
            pady=5
    )

        # ================= BOTTOM AREA (STATUS BAR) =================
        self.status_bar = StatusBar(self)
        self.status_bar.grid(row=1, column=0, columnspan=2, sticky="ew")

    # ================= Logic (preventing GUI freeze)=================
    def _start_connect_thread(self):
        """Start a separate network thread to avoid freezing the interface."""
        # Lock the button to prevent multiple clicks.
        self.connect_btn.configure(state="disabled")
        self.status_bar.set_status("⏳ Setting up P2P network...", "#f9e2af")
        
        # Move the network waiting process to a different thread.
        threading.Thread(target=self._network_connect_task, daemon=True).start()

    def _network_connect_task(self):
        """Simulating socket handling functions (Running in the background)"""
        # TODO: Place the actual socket.bind() or socket.connect() code here.
        time.sleep(1.5) # Simulate 1.5s delay to open port
        
        # After the network is ready, request the main thread to update the UI
        self.after(0, self._on_connected)

    def _on_connected(self):
        """Update UI after the network is ready"""
        self.status_bar.set_status("✅ Ready to send and receive messages", "#a6e3a1")
        
        # Change the connect button to a disconnect button
        self.connect_btn.configure(
            text="✂️ Disconnect", state="normal", 
            fg_color="#f38ba8", hover_color="#d76f8c"
        )
        
        # Unlock message input field (clear UX)
        self.msg_entry.configure(state="normal")
        self.send_btn.configure(state="normal")

        # Simulate loading a list of peers to display in the sidebar.
        self.sidebar.update_peers(["James", "Alice"])
    
    def send_file(self, file_path):
        """This function is called when you click the 'Send File' button on the UI"""
        print(f"Sending file: {file_path}")

    def cancel_transfer(self, transfer_id):
        """This function is called when you click the 'Cancel' button on the UI"""
        print(f"Cancelling transfer: {transfer_id}")
        self.on_transfer_complete(transfer_id)

