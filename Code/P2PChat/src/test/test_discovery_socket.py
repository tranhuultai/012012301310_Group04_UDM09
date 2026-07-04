from unittest.mock import MagicMock

from network.discovery import (
    DiscoveryService,
    DISCOVERY,
    DISCOVERY_RESPONSE
)

from security.rsa_utils import RSAUtils


def create_discovery():

    private_key, public_key = (
        RSAUtils.generate_key_pair()
    )

    return DiscoveryService(
        username="Tai",
        listen_port=5000,
        peer_id="peer1",
        fingerprint="FP1",
        public_key_pem=RSAUtils.serialize_public_key(
            public_key
        ),
        private_key_pem=RSAUtils.serialize_private_key(
            private_key
        )
    )


def test_get_nearby_peers_empty():

    discovery = create_discovery()

    peers = discovery.get_nearby_peers()

    assert peers == {}


def test_get_nearby_peers_copy():

    discovery = create_discovery()

    discovery.nearby_peers["peer1"] = {
        "username": "Alice"
    }

    peers = discovery.get_nearby_peers()

    assert peers["peer1"]["username"] == "Alice"


def test_handle_unknown_packet():

    discovery = create_discovery()

    discovery._handle_packet(
        {
            "type": "UNKNOWN"
        },
        ("127.0.0.1", 5000)
    )

    assert True


def test_handle_own_discovery_echo():

    discovery = create_discovery()

    discovery._send_response = MagicMock()

    discovery._handle_packet(
        {
            "type": DISCOVERY,
            "instance_id": discovery.instance_id
        },
        ("127.0.0.1", 5000)
    )

    discovery._send_response.assert_not_called()


def test_handle_discovery_packet():

    discovery = create_discovery()

    discovery._send_response = MagicMock()

    discovery._handle_packet(
        {
            "type": DISCOVERY,
            "instance_id": "other"
        },
        ("127.0.0.1", 5000)
    )

    discovery._send_response.assert_called_once()


def test_handle_discovery_response_packet():

    discovery = create_discovery()

    discovery.update_peer_registry = MagicMock()

    discovery._handle_packet(
        {
            "type": DISCOVERY_RESPONSE,
            "peer_id": "peer2"
        },
        ("127.0.0.1", 5000)
    )

    discovery.update_peer_registry.assert_called_once()


def test_on_peer_found_called():

    discovery = create_discovery()

    called = []

    def callback(packet, address):
        called.append(packet)

    discovery.on_peer_found = callback

    discovery._handle_packet(
        {
            "type": DISCOVERY_RESPONSE,
            "peer_id": "peer2"
        },
        ("127.0.0.1", 5000)
    )

    assert len(called) == 1


def test_send_response_without_socket():

    discovery = create_discovery()

    discovery._socket = None

    discovery._send_response(
        ("127.0.0.1", 5000)
    )

    assert True


def test_send_response_success():

    discovery = create_discovery()

    fake_socket = MagicMock()

    discovery._socket = fake_socket

    discovery._send_response(
        ("127.0.0.1", 5000)
    )

    fake_socket.sendto.assert_called_once()


def test_send_response_socket_error():

    discovery = create_discovery()

    fake_socket = MagicMock()

    fake_socket.sendto.side_effect = OSError()

    discovery._socket = fake_socket

    discovery._send_response(
        ("127.0.0.1", 5000)
    )

    assert True


def test_cleanup_expired_no_peers():

    discovery = create_discovery()

    discovery.cleanup_expired_peers()

    assert discovery.nearby_peers == {}


def test_stop_without_start():

    discovery = create_discovery()

    discovery.stop()

    assert discovery.running is False


def test_listen_loop_ignores_non_dict_json():
    """A non-dict JSON payload (e.g. "null") used to crash this loop via
    packet.get() — it must now be skipped instead."""

    discovery = create_discovery()
    discovery.running = True
    discovery._handle_packet = MagicMock()

    call_count = {"n": 0}

    class FakeUdpSocket:
        def recvfrom(self, bufsize):  # pylint: disable=unused-argument
            call_count["n"] += 1
            if call_count["n"] == 1:
                return (b"null", ("127.0.0.1", 5000))
            discovery.running = False
            raise OSError("stop test loop")

    discovery._listen_sock = FakeUdpSocket()

    discovery._listen_loop()  # must return normally, not raise

    discovery._handle_packet.assert_not_called()