"""Team pairing codes (D-008)."""
import base64
import hashlib
import hmac


def pairing_code(secret: str, team_id: str) -> str:
    """First 6 chars of base32(HMAC-SHA256(secret, team_id)), uppercase."""
    digest = hmac.new(secret.encode(), team_id.encode(), hashlib.sha256).digest()
    return base64.b32encode(digest).decode().rstrip("=")[:6].upper()
