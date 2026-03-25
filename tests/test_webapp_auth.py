from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse
from unittest import TestCase

from proxypulse.api.webapp import validate_telegram_webapp_init_data
from proxypulse.core.webapp_auth import build_webapp_access_token, validate_webapp_access_token


def build_init_data(user: dict, bot_token: str) -> str:
    pairs = {
        "auth_date": "1710000000",
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(user, separators=(",", ":"), ensure_ascii=False),
    }
    data_check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(pairs)


class WebAppAuthTests(TestCase):
    def test_validate_telegram_webapp_init_data(self) -> None:
        user = {"id": 123456789, "first_name": "Mars"}
        init_data = build_init_data(user, "123456:ABCDEF")

        parsed_user = validate_telegram_webapp_init_data(init_data, "123456:ABCDEF")

        self.assertEqual(parsed_user["id"], 123456789)

    def test_validate_webapp_access_token(self) -> None:
        token = build_webapp_access_token(123456789, "123456:ABCDEF")

        self.assertTrue(validate_webapp_access_token(123456789, token, "123456:ABCDEF"))
        self.assertFalse(validate_webapp_access_token(987654321, token, "123456:ABCDEF"))
