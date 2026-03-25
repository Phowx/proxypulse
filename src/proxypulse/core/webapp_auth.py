from __future__ import annotations

import hashlib
import hmac


def build_webapp_access_token(user_id: int, bot_token: str) -> str:
    return hmac.new(bot_token.encode("utf-8"), f"webapp:{user_id}".encode("utf-8"), hashlib.sha256).hexdigest()


def validate_webapp_access_token(user_id: int, token: str, bot_token: str) -> bool:
    expected = build_webapp_access_token(user_id, bot_token)
    return hmac.compare_digest(expected, token)
