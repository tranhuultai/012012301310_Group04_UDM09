"""Tests for StatusBar — bottom bar with 5 status segments."""
import pytest
import customtkinter as ctk

from gui.statusbar import StatusBar


@pytest.fixture
def root():
    """Provide a hidden Tk root window and destroy it after the test."""
    root = ctk.CTk()
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def create_statusbar(root):
    """Helper: create a StatusBar attached to *root*."""
    return StatusBar(root)


def test_default_labels(root):
    """Freshly created StatusBar must show initializing text and zero peers."""
    bar = create_statusbar(root)
    assert bar._status_label.cget("text") == "🔄 Initializing..."
    assert bar._stats_label.cget("text") == "Peers: 0"


def test_set_status(root):
    """set_status must update the primary status label text."""
    bar = create_statusbar(root)
    bar.set_status("Online", "#ffffff")
    assert bar._status_label.cget("text") == "Online"


def test_set_stats(root):
    """set_stats must format the stats label with peer/connected/contacts counts."""
    bar = create_statusbar(root)
    bar.set_stats(peers=5, connected=1, contacts=2)
    assert bar._stats_label.cget("text") == "Peers:5 | Connected:1 | Contacts:2  "
