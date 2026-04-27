"""Mapping stores for original-to-replacement values.

Two implementations:

* :class:`InMemoryMappingStore` — used for document-level consistency.
* :class:`SqliteMappingStore` — backs batch/project consistency. We store an
  HMAC of the original value as the lookup key, *not* the raw value, so the
  persistent store never contains plaintext PII by default.

The HMAC key is derived from a per-installation secret (env var
``IDP_ANONYMISER_HMAC_KEY``) or, if not set, a fixed seed (with a warning).
This keeps the store usable in dev while allowing operators to rotate the key
in production.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sqlite3
from abc import ABC, abstractmethod
from contextlib import closing
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


_DEFAULT_HMAC_SECRET = b"idp-anonymiser-default-key-rotate-me"


def _get_hmac_secret() -> bytes:
    env = os.environ.get("IDP_ANONYMISER_HMAC_KEY")
    if env:
        return env.encode("utf-8")
    return _DEFAULT_HMAC_SECRET


def hash_value(original: str, scope: str = "document") -> str:
    """Return a stable HMAC-SHA256 hex digest for ``original`` under ``scope``.

    The scope is folded into the HMAC so the same value in different scopes
    (document vs project) yields different keys — this lets us layer scopes
    without leaking from one to another.
    """
    msg = f"{scope}\u0001{original}".encode("utf-8")
    return hmac.new(_get_hmac_secret(), msg, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class MappingStore(ABC):
    """Lookup of (entity_type, original_hash) -> replacement_value."""

    @abstractmethod
    def get(self, entity_type: str, original_hash: str) -> Optional[str]:
        ...

    @abstractmethod
    def put(self, entity_type: str, original_hash: str, replacement: str) -> None:
        ...

    @abstractmethod
    def all(self) -> dict[tuple[str, str], str]:
        ...


# ---------------------------------------------------------------------------
# In-memory
# ---------------------------------------------------------------------------


class InMemoryMappingStore(MappingStore):
    def __init__(self) -> None:
        self._d: dict[tuple[str, str], str] = {}

    def get(self, entity_type: str, original_hash: str) -> Optional[str]:
        return self._d.get((entity_type, original_hash))

    def put(self, entity_type: str, original_hash: str, replacement: str) -> None:
        self._d[(entity_type, original_hash)] = replacement

    def all(self) -> dict[tuple[str, str], str]:
        return dict(self._d)


# ---------------------------------------------------------------------------
# SQLite (used for batch/project consistency)
# ---------------------------------------------------------------------------


class SqliteMappingStore(MappingStore):
    """SQLite-backed mapping store keyed by HMAC hashes.

    Schema::

        CREATE TABLE replacements (
          entity_type TEXT NOT NULL,
          original_hash TEXT NOT NULL,
          replacement TEXT NOT NULL,
          created_at REAL NOT NULL,
          PRIMARY KEY (entity_type, original_hash)
        )
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, isolation_level=None)

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS replacements (
                    entity_type TEXT NOT NULL,
                    original_hash TEXT NOT NULL,
                    replacement TEXT NOT NULL,
                    created_at REAL NOT NULL DEFAULT (julianday('now')),
                    PRIMARY KEY (entity_type, original_hash)
                )
                """
            )

    def get(self, entity_type: str, original_hash: str) -> Optional[str]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT replacement FROM replacements WHERE entity_type=? AND original_hash=?",
                (entity_type, original_hash),
            ).fetchone()
            return row[0] if row else None

    def put(self, entity_type: str, original_hash: str, replacement: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO replacements(entity_type, original_hash, replacement) VALUES (?, ?, ?)",
                (entity_type, original_hash, replacement),
            )

    def all(self) -> dict[tuple[str, str], str]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT entity_type, original_hash, replacement FROM replacements"
            ).fetchall()
        return {(et, oh): r for et, oh, r in rows}


__all__ = [
    "MappingStore",
    "InMemoryMappingStore",
    "SqliteMappingStore",
    "hash_value",
]
