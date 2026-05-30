import customtkinter as ctk
from gui.validation import validate_ip, validate_port
from node.core import P2PNode

class ChatApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()

        self.connected_peers: list[str] = []
        self.selected_peer: str | None = None
        
        self.node = P2PNode(
            host="0.0.0.0",
            port=12000,  # Default port for P2P chat
            on_message=self.handle_peer_message,
            on_disconnect=self.handle_disconnect
        )
        
        self.node.start_server()

        self.setup_window()
        self.setup_layout()
        self.add_system_message("P2P Node started. Waiting for connections...")

    # Layout and event handlers
    def setup_window(self) -> None:
        """Configure the main application window."""

        self.title("UDM_09 · P2P Chat GUI")
        self.geometry("1000x600")
        self.minsize(900, 500)

    def setup_layout(self) -> None:
        """Create the main application layout."""

        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.create_chat_section()
        self.create_sidebar()

    def create_chat_section(self) -> None:
        """Create the chat display section."""

        self.chat_frame = ctk.CTkFrame(self)

        self.chat_frame.grid(
            row=0,
            column=0,
            padx=(10, 5),
            pady=10,
            sticky="nsew"
        )

        self.chat_frame.grid_rowconfigure(0, weight=1)
        self.chat_frame.grid_columnconfigure(0, weight=1)

        # Chat display
        self.chat_box = ctk.CTkTextbox(
            self.chat_frame,
            corner_radius=10
        )

        self.chat_box.grid(
            row=0,
            column=0,
            padx=10,
            pady=(10, 5),
            sticky="nsew"
        )

        # Message input
        self.message_entry = ctk.CTkEntry(
            self.chat_frame,
            placeholder_text="Enter message..."
        )

        self.broadcast_button = ctk.CTkButton(
            self.chat_frame,
            text="📡 Broadcast",
            command=self.broadcast_message
        )

        self.broadcast_button.grid(
            row=1,
            column=1,
            padx=(5, 10),
            pady=(5, 10),
            sticky="ew"
        )

        self.message_entry.grid(
            row=1,
            column=0,
            padx=10,
            pady=(5, 10),
            sticky="ew"
        )

        self.message_entry.bind(
            "<Return>",
            self.handle_enter
        )

    def create_sidebar(self) -> None:
        """Create the peer management sidebar."""

        self.sidebar_frame = ctk.CTkFrame(self)

        self.sidebar_frame.grid(
            row=0,
            column=1,
            padx=(5, 10),
            pady=10,
            sticky="nsew"
        )

        self.peer_label = ctk.CTkLabel(
            self.sidebar_frame,
            text="Connected Peers",
            font=("Arial", 18, "bold")
        )

        self.peer_label.pack(pady=(15, 10))

        # Peer list
        self.peer_listbox = ctk.CTkTextbox(
            self.sidebar_frame,
            width=250,
            height=250
        )

        self.peer_listbox.pack(
            padx=10,
            pady=5,
            fill="both",
            expand=True
        )
        self.peer_listbox.bind("<ButtonRelease-1>", self.select_peer)


        # Peer IP input
        self.ip_entry = ctk.CTkEntry(
            self.sidebar_frame,
            placeholder_text="Peer IP"
        )

        self.ip_entry.pack(
            padx=10,
            pady=(10, 5),
            fill="x"
        )

        # Peer port input
        self.port_entry = ctk.CTkEntry(
            self.sidebar_frame,
            placeholder_text="Port"
        )

        self.port_entry.pack(
            padx=10,
            pady=5,
            fill="x"
        )

        self.connect_button = ctk.CTkButton(
            self.sidebar_frame,
            text="Connect",
            command=self.connect_to_peer
        )

        self.connect_button.pack(
            padx=10,
            pady=(10, 5),
            fill="x"
        )

        self.send_button = ctk.CTkButton(
            self.sidebar_frame,
            text="Send",
            command=self.send_message
        )

        self.send_button.pack(
            padx=10,
            pady=(5, 15),
            fill="x"
        )

    # Event handlers
    def connect_to_peer(self) -> None:
        """Handle the Connect button."""

        ip = self.ip_entry.get().strip()
        port = self.port_entry.get().strip()

        if not ip or not port:
            self.add_system_message("Please enter peer IP and port.")
            return

        if not validate_ip(ip):
            self.add_system_message("Invalid IP address format.")
            return

        if not validate_port(port):
            self.add_system_message("Invalid port number. Must be 1 - 65535.")
            return

        self.add_system_message(f"Connecting to {ip}:{port}…")

        if not self.node.connect_to_peer(ip, int(port)):
            self.add_system_message(f"Could not connect to {ip}:{port}.")
            return

        peer_address = f"{ip}:{port}"

        if peer_address not in self.connected_peers:
            self.connected_peers.append(peer_address)
            self.update_peer_list()
            self.add_system_message("Connected. Waiting for handshake…")

    def send_message(self) -> None:
        """Handle the Send button."""

        message = self.message_entry.get().strip()
        if not message:
            return

        if not self.connected_peers:
            self.add_system_message("No connected peers.")
            return

        if self.selected_peer is None:
            self.add_system_message("No peer selected.")
            return

        self.node.send_message(message, self.selected_peer)
        self.chat_box.insert("end", f"You: {message}\n")
        self.chat_box.see("end")
        self.message_entry.delete(0, "end")

    def broadcast_message(self) -> None:
        """Handle the Broadcast button."""
        message = self.message_entry.get().strip()
        if not message:
            return

        if not self.connected_peers:
            self.add_system_message("No connected peers.")
            return

        self.chat_box.insert("end", f"You (broadcast): {message}\n")
        self.chat_box.see("end")
        self.message_entry.delete(0, "end")

        self.node.broadcast_message(message)
        
    def handle_enter(self, _event) -> None:
        self.send_message()

    def update_peer_list(self) -> None:
        """Refresh the connected-peers display in the sidebar."""

        self.peer_listbox.delete("1.0", "end")

        for peer in self.connected_peers:
            self.peer_listbox.insert("end", f"{peer}\n")

    def select_peer(self, event) -> None:  
        """Handle peer selection in the listbox."""

        selected = self.peer_listbox.get("insert linestart", "insert lineend").strip()

        if selected:
            self.selected_peer = selected
            self.add_system_message(f"Selected peer: {selected}")

    # Message handling (called from networking thread)
    def display_peer_message(self, message: str | dict) -> None:
        """Display a message received from a peer."""

        text = message if isinstance(message, str) else str(message)
        self.chat_box.insert("end", f"Peer: {text}\n")
        self.chat_box.see("end")

    def add_system_message(self, message: str) -> None:
        """Display a system message in the chat box."""
        
        self.chat_box.insert("end", f"[SYSTEM] {message}\n")
        self.chat_box.see("end")

    def handle_peer_message(self, message: str | dict) -> None:
        """Handle a message received from a peer (called from networking thread)."""

        def update() -> None:
            self.display_peer_message(message)

        self.after(0, update)

    def handle_disconnect(self, peer_address: str) -> None:
        """Handle a peer disconnection event (called from networking thread)."""

        def update() -> None:
            if peer_address in self.connected_peers:
                self.connected_peers.remove(peer_address)
                self.update_peer_list()
            self.add_system_message(f"Connection lost: {peer_address}")

        self.after(0, update)