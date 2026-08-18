import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

import security


def test_refresh_token_round_trip():
    refresh_token = security.create_refresh_token({"sub": "user-123"})
    payload = security.decode_refresh_token(refresh_token)

    assert payload["sub"] == "user-123"
    assert payload["type"] == "refresh"
