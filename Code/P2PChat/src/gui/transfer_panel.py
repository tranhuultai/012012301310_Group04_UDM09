import os
from tkinter import filedialog

import customtkinter as ctk

class TransferPanel(ctk.CTkFrame):

    def __init__(self, master, controller=None, **kwargs):
        super().__init__(master, **kwargs)

        self.controller = controller

        # transfer_id -> widgets
        self.transfers = {}

        self._build_ui()

    # UI

    def _build_ui(self):

        title = ctk.CTkLabel(
            self,
            text="Active Transfers",
            font=("Arial", 16, "bold")
        )
        title.pack(pady=(10, 5))

        self.transfer_container = ctk.CTkScrollableFrame(
            self,
            height=250
        )
        self.transfer_container.pack(
            fill="both",
            expand=True,
            padx=5,
            pady=5
        )

        self.send_btn = ctk.CTkButton(
            self,
            text="📎 Send File",
            command=self._send_file
        )
        self.send_btn.pack(
            fill="x",
            padx=5,
            pady=(5, 10)
        )

    # SEND FILE
    def _send_file(self):

        filepath = filedialog.askopenfilename()

        if not filepath:
            return

        if (
            self.controller
            and hasattr(self.controller, "send_file")
        ):
            self.controller.send_file(filepath)

    # ADD TRANSFER
    def add_transfer(
        self,
        transfer_id,
        filename,
        peer_name,
        direction="out"
    ):

        if transfer_id in self.transfers:
            return

        card = ctk.CTkFrame(
            self.transfer_container
        )
        card.pack(
            fill="x",
            padx=5,
            pady=5
        )

        if direction == "out":
            icon = "📤"
            arrow = "→"
        else:
            icon = "📥"
            arrow = "←"

        title = ctk.CTkLabel(
            card,
            text=f"{icon} {filename} {arrow} {peer_name}",
            anchor="w"
        )
        title.pack(
            fill="x",
            padx=10,
            pady=(8, 2)
        )

        row = ctk.CTkFrame(
            card,
            fg_color="transparent"
        )
        row.pack(
            fill="x",
            padx=10,
            pady=(2, 8)
        )

        progress = ctk.CTkProgressBar(
            row
        )
        progress.pack(
            side="left",
            fill="x",
            expand=True
        )

        progress.set(0)

        percent_label = ctk.CTkLabel(
            row,
            text="0%"
        )
        percent_label.pack(
            side="left",
            padx=8
        )

        cancel_btn = ctk.CTkButton(
            row,
            text="Cancel",
            width=70,
            command=lambda:
                self._cancel_transfer(
                    transfer_id
                )
        )
        cancel_btn.pack(
            side="right"
        )

        self.transfers[transfer_id] = {
            "frame": card,
            "progress": progress,
            "percent": percent_label
        }

    # UPDATE PROGRESS

    def update_transfer(
        self,
        transfer_id,
        progress_value
    ):

        if transfer_id not in self.transfers:
            return

        progress_value = max(
            0,
            min(1, progress_value)
        )

        transfer = self.transfers[
            transfer_id
        ]

        transfer["progress"].set(
            progress_value
        )

        transfer["percent"].configure(
            text=f"{int(progress_value * 100)}%"
        )

    # REMOVE

    def remove_transfer(
        self,
        transfer_id
    ):

        if transfer_id not in self.transfers:
            return

        self.transfers[
            transfer_id
        ]["frame"].destroy()

        del self.transfers[
            transfer_id
        ]

    # CANCEL

    def _cancel_transfer(
        self,
        transfer_id
    ):

        if (
            self.controller
            and hasattr(
                self.controller,
                "cancel_transfer"
            )
        ):
            self.controller.cancel_transfer(
                transfer_id
            )

        self.remove_transfer(
            transfer_id
        )

# TEST

if __name__ == "__main__":

    class DummyController:

        def send_file(self, filepath):
            print(
                "Send:",
                os.path.basename(filepath)
            )

        def cancel_transfer(
            self,
            transfer_id
        ):
            print(
                "Cancelled:",
                transfer_id
            )

    ctk.set_appearance_mode("dark")

    app = ctk.CTk()
    app.geometry("450x500")

    panel = TransferPanel(
        app,
        controller=DummyController()
    )

    panel.pack(
        fill="both",
        expand=True,
        padx=10,
        pady=10
    )

    panel.add_transfer(
        "id_1",
        "report.pdf",
        "Alice",
        "out"
    )

    panel.add_transfer(
        "id_2",
        "photo.jpg",
        "Bob",
        "in"
    )

    panel.update_transfer(
        "id_1",
        0.82
    )

    panel.update_transfer(
        "id_2",
        0.41
    )

    app.mainloop()
