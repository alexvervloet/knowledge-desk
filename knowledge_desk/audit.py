"""Append-only audit log. Records who did what, in which org, and when, so an
org admin can review activity.

Writes are best-effort: an audit failure logs a warning but never propagates,
because losing the ability to record an event must not take down the action the
user was performing. The trade-off is that a dropped write is a gap in the log,
not a failed request.
"""

from __future__ import annotations

import sys
from typing import Any

from psycopg.types.json import Json

from knowledge_desk import pii
from knowledge_desk.db import connect


def log(
    org_id: str,
    actor_user_id: str | None,
    action: str,
    detail: dict[str, Any] | None = None,
) -> None:
    try:
        safe_detail = pii.redact_detail(detail or {})
        with connect() as conn:
            conn.execute(
                "insert into audit_log(org_id, actor_user_id, action, detail)"
                " values (%s, %s, %s, %s)",
                (org_id, actor_user_id, action, Json(safe_detail)),
            )
    except Exception as exc:  # noqa: BLE001 - audit must never break the request
        print(f"[audit] failed to record {action}: {exc}", file=sys.stderr)
