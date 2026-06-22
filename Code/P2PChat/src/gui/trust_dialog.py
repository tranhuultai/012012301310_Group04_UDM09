import customtkinter as ctk

class TrustDialog(ctk.CTkToplevel):
    """
    Trust On First Use (TOFU) Dialog

    Modes:
        - new_peer
        - warning

    Callback results:
        - trust
        - block
        - skip
        - update
    """

    def __init__(
        self,
        master,
        mode,
        peer_info,
        callback=None
    ):
        super().__init__(master)

        self.mode = mode
        self.peer_info = peer_info
        self.callback = callback

        self._setup_window()

        if self.mode == "new_peer":
            self._build_new_peer_ui()

        elif self.mode == "warning":
            self._build_warning_ui()

        else:
            raise ValueError(
                f"Unsupported mode: {self.mode}"
            )

    # WINDOW 

    def _setup_window(self):
        self.title("Peer Trust Verification")
        self.geometry("520x320")
        self.resizable(False, False)

        self.transient(self.master)
        self.grab_set()

        self.grid_columnconfigure(0, weight=1)

    # NEW PEER

    def _build_new_peer_ui(self):

        title = ctk.CTkLabel(
            self,
            text="🔑 New Peer",
            font=("Arial", 20, "bold")
        )
        title.pack(pady=(20, 10))

        card = ctk.CTkFrame(self)
        card.pack(fill="x", padx=20)

        username = self.peer_info.get(
            "username",
            "Unknown"
        )

        peer_id = self.peer_info.get(
            "peer_id",
            "-"
        )

        fingerprint = self.peer_info.get(
            "fingerprint",
            "-"
        )

        ctk.CTkLabel(
            card,
            text=f"Username:    {username}",
            anchor="w"
        ).pack(fill="x", padx=15, pady=(15, 5))

        ctk.CTkLabel(
            card,
            text=f"Peer ID:     {peer_id}",
            anchor="w"
        ).pack(fill="x", padx=15, pady=5)

        ctk.CTkLabel(
            card,
            text=f"Fingerprint: {fingerprint}",
            anchor="w"
        ).pack(fill="x", padx=15, pady=(5, 15))

        ctk.CTkLabel(
            self,
            text=(
                "Verify fingerprint with peer\n"
                "before trusting."
            ),
            justify="center"
        ).pack(pady=20)

        btn_frame = ctk.CTkFrame(
            self,
            fg_color="transparent"
        )
        btn_frame.pack(pady=10)

        ctk.CTkButton(
            btn_frame,
            text="Trust & Connect",
            width=140,
            command=lambda: self._send_result(
                "trust"
            )
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            btn_frame,
            text="Block",
            width=100,
            command=lambda: self._send_result(
                "block"
            )
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            btn_frame,
            text="Skip",
            width=100,
            command=lambda: self._send_result(
                "skip"
            )
        ).pack(side="left", padx=5)

    # WARNING

    def _build_warning_ui(self):

        title = ctk.CTkLabel(
            self,
            text="⚠️ WARNING: Fingerprint Changed",
            font=("Arial", 18, "bold")
        )
        title.pack(pady=(20, 10))

        card = ctk.CTkFrame(self)
        card.pack(fill="x", padx=20)

        known_fp = self.peer_info.get(
            "known_fingerprint",
            "-"
        )

        current_fp = self.peer_info.get(
            "current_fingerprint",
            "-"
        )

        ctk.CTkLabel(
            card,
            text=f"Known:     {known_fp}",
            anchor="w"
        ).pack(fill="x", padx=15, pady=(15, 5))

        ctk.CTkLabel(
            card,
            text=f"Current:   {current_fp}",
            anchor="w"
        ).pack(fill="x", padx=15, pady=(5, 15))

        ctk.CTkLabel(
            self,
            text=(
                "This may indicate a MITM attack.\n"
                "Verify the fingerprint before trusting."
            ),
            justify="center"
        ).pack(pady=20)

        btn_frame = ctk.CTkFrame(
            self,
            fg_color="transparent"
        )
        btn_frame.pack(pady=10)

        ctk.CTkButton(
            btn_frame,
            text="Update & Trust",
            width=150,
            command=lambda: self._send_result(
                "update"
            )
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            btn_frame,
            text="Block",
            width=120,
            command=lambda: self._send_result(
                "block"
            )
        ).pack(side="left", padx=10)

    # RESULT
    def _send_result(self, result):

        if self.callback:
            self.callback(result)

        self.destroy()

# TEST

if __name__ == "__main__":

    ctk.set_appearance_mode("dark")

    app = ctk.CTk()
    app.geometry("500x300")

    def handle_result(result):
        print("Selected:", result)

    def show_new_peer():
        TrustDialog(
            master=app,
            mode="new_peer",
            peer_info={
                "username": "Alice",
                "peer_id": "a3f9-7b2d",
                "fingerprint": "AB:CD:EF:12:34:56"
            },
            callback=handle_result
        )

    def show_warning():
        TrustDialog(
            master=app,
            mode="warning",
            peer_info={
                "known_fingerprint":
                    "AB:CD:EF:12:34:56",
                "current_fingerprint":
                    "99:AA:BB:CC:DD:EE"
            },
            callback=handle_result
        )

    ctk.CTkButton(
        app,
        text="Test New Peer",
        command=show_new_peer
    ).pack(pady=20)

    ctk.CTkButton(
        app,
        text="Test Warning",
        command=show_warning
    ).pack(pady=10)

    app.mainloop()
def on_peer_discovered(
    self,
    peer_info
):

    from gui.trust_dialog import TrustDialog

    TrustDialog(
        master=self,
        mode="new_peer",
        peer_info=peer_info,
        callback=self.handle_trust_result
    )
def handle_trust_result(
    self,
    result
):
    print(
        "Trust Result:",
        result
    )