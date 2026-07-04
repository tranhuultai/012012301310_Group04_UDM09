"""Input validation helpers for network parameters."""
# WHY changed: validate_port raised an uncaught ValueError on Unicode "digit"
# strings isdigit() accepts but int() rejects, crashing main.py's argv parsing.

import ipaddress

_RESERVED_IPS: frozenset[str] = frozenset({
    "0.0.0.0",          # unspecified address
    "255.255.255.255",  # limited broadcast
})


def validate_ip(ip: str) -> bool:
    """Return True if ip is a valid, non-reserved IPv4 address."""
    if ip in _RESERVED_IPS:
        return False
    try:
        ipaddress.IPv4Address(ip)
        return True
    except ValueError:
        return False


def validate_port(port: str) -> bool:
    """Return True if port is a digit string in range 1-65535."""
    if not port.isdigit():
        return False
    # isdigit() accepts some Unicode digits (e.g. "²") that int() rejects.
    try:
        return 1 <= int(port) <= 65535
    except ValueError:
        return False
