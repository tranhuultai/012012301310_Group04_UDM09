"""JWT identity token for P2PChat."""

from datetime import datetime, timedelta, timezone

import jwt

from config import JWT_EXPIRE_HOURS


class JWTHandler:
    """Creates and verifies RS256-signed identity tokens for peer discovery."""

    @staticmethod
    def create_identity_token(
        peer_id: str,
        username: str,
        fingerprint: str,
        private_key_pem: str,
    ) -> str:
        """Create a signed RS256 identity token for the given peer identity."""
        now = datetime.now(timezone.utc)
        payload = {
            "peer_id":     peer_id,
            "username":    username,
            "fingerprint": fingerprint,
            "iat":         now,
            "exp":         now + timedelta(hours=JWT_EXPIRE_HOURS),
        }
        return jwt.encode(payload, private_key_pem, algorithm="RS256")

    @staticmethod
    def verify_identity_token(token: str, public_key_pem: str) -> dict | None:
        """Verify a signed RS256 identity token, returning its claims or None."""
        try:
            return jwt.decode(token, public_key_pem, algorithms=["RS256"])
        except jwt.InvalidTokenError:
            return None
