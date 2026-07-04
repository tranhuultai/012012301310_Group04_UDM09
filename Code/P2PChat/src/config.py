"""Application configuration."""

import logging

# =========================
# Network
# =========================

DEFAULT_HOST = "0.0.0.0"

DEFAULT_LISTEN_PORT = 12000
DISCOVERY_PORT = 15000

MAX_PACKET_SIZE = 131072   # 128 KB — must fit one 64 KB file chunk after Fernet + Base64

# Handshake "version" field — both sides send this; a mismatch is rejected
# instead of silently trusting a diverged packet format.
PROTOCOL_VERSION = "1.0"

# =========================
# File Transfer
# =========================

FILE_CHUNK_SIZE   = 65536   # 64 KB raw chunk size before encryption/encoding
FILE_MAX_SIZE     = 10 * 1024 * 1024  # 10 MB hard limit
FILE_ALLOWED_EXT  = frozenset({".pdf", ".docx", ".txt",
                                ".png", ".jpg", ".jpeg", ".zip"})
FILE_DOWNLOAD_DIR = "downloads"   # default save directory (relative to cwd)
SOCKET_TIMEOUT = 1

# =========================
# Discovery
# =========================

PRESENCE_INTERVAL = 5
PEER_TIMEOUT = 15

# =========================
# Security
# =========================

JWT_EXPIRE_HOURS = 12

# =========================
# GUI
# =========================

WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 800

# =========================
# Logging
# =========================

LOG_FORMAT = (
    "%(asctime)s "
    "%(levelname)s "
    "%(name)s: "
    "%(message)s"
)

LOG_LEVEL = logging.INFO


def configure_logging() -> None:
    """Configure application logging."""

    logging.basicConfig(
        level=LOG_LEVEL,
        format=LOG_FORMAT
    )
