"""Password hashing and session tokens.

Passwords: bcrypt over a sha256 pre-hash so passwords longer than bcrypt's
72-byte input limit are not silently truncated (a real correctness bug: two
distinct long passwords could otherwise collide).

Sessions: the client holds an opaque random bearer token; the database stores
only its sha256, so a database read cannot recover a live token.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from functools import cache

import bcrypt

# owner > admin > member. require_role compares these ranks.
ROLE_RANK = {"member": 1, "admin": 2, "owner": 3}


def _prehash(password: str) -> bytes:
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), password_hash.encode("ascii"))
    except ValueError:
        return False


@cache
def dummy_hash() -> str:
    """A real bcrypt hash of a value nothing can log in with.

    Verifying against this on the user-not-found path is what stops login from
    answering "does this account exist" through its own response time. Skipping
    bcrypt when there is no user made a miss finish in about 4ms against roughly
    240ms for a hit, which is a clean oracle needing no error-message difference
    to read. Cached because generating it costs exactly as much as the
    verification it is standing in for.
    """
    return hash_password(secrets.token_urlsafe(32))


def new_session_token() -> tuple[str, str]:
    """Return (raw_token_for_client, token_hash_for_storage)."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def role_at_least(actual: str, required: str) -> bool:
    return ROLE_RANK.get(actual, 0) >= ROLE_RANK.get(required, 99)
