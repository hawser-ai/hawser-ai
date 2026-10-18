"""Database backends for the server: Postgres (pg8000) and SQLite (single node).

Both implement :class:`hawser.storage.sql.SqlBackend`, so the metadata store, job queue, auth
and webhook tables share one SQL text. Postgres gets per-run advisory locks and
``FOR UPDATE SKIP LOCKED``; SQLite serialises with ``BEGIN IMMEDIATE``.
"""

from __future__ import annotations

import contextlib
import queue
import threading
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import pg8000.dbapi

from hawser.storage.sql import Row, SqlBackend, SQLiteBackend, Tx


class _PgTx(Tx):
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Sequence[Any] = ()) -> list[Row]:
        cur = self._conn.cursor()
        cur.execute(sql.replace("?", "%s"), tuple(params))
        if cur.description is None:
            return []
        return [tuple(r) for r in cur.fetchall()]

    def lock_run(self, run_id: str) -> None:
        self.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (run_id,))


class PostgresBackend(SqlBackend):
    skip_locked = " FOR UPDATE SKIP LOCKED"

    def __init__(self, url: str, *, pool_size: int = 8) -> None:
        self.url = url
        self.pool_size = pool_size
        self._kwargs = connect_kwargs(url)
        self._idle: queue.LifoQueue[Any] = queue.LifoQueue()
        self._created = 0
        self._lock = threading.Lock()

    def _connect(self) -> Any:
        conn = pg8000.dbapi.connect(**self._kwargs)
        conn.autocommit = False
        return conn

    def _acquire(self) -> Any:
        try:
            return self._idle.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            if self._created < self.pool_size:
                self._created += 1
                try:
                    return self._connect()
                except Exception:
                    self._created -= 1
                    raise
        return self._idle.get(timeout=30)

    def _release(self, conn: Any, *, broken: bool = False) -> None:
        if broken:
            with self._lock:
                self._created -= 1
            with contextlib.suppress(Exception):
                conn.close()
            return
        self._idle.put(conn)

    @contextmanager
    def transaction(self, *, write: bool = False) -> Generator[Tx]:
        conn = self._acquire()
        broken = False
        try:
            yield _PgTx(conn)
            conn.commit()
        except BaseException:
            try:
                conn.rollback()
            except Exception:
                broken = True
            raise
        finally:
            self._release(conn, broken=broken)

    def ping(self) -> bool:
        with self.transaction() as tx:
            return tx.execute("SELECT 1")[0][0] == 1

    def close(self) -> None:
        while True:
            try:
                conn = self._idle.get_nowait()
            except queue.Empty:
                return
            with contextlib.suppress(Exception):
                conn.close()


def connect_kwargs(url: str) -> dict[str, Any]:
    parts = urlsplit(url)
    if parts.scheme not in ("postgresql", "postgres"):
        raise ValueError(f"not a postgres url: {url!r}")
    q = parse_qs(parts.query)
    kwargs: dict[str, Any] = {
        "user": unquote(parts.username or "postgres"),
        "password": unquote(parts.password) if parts.password else None,
        "database": parts.path.lstrip("/") or "postgres",
    }
    sock_dir = q.get("host", [None])[0]
    port = parts.port or 5432
    if sock_dir and sock_dir.startswith("/"):
        kwargs["unix_sock"] = f"{sock_dir}/.s.PGSQL.{port}"
    else:
        kwargs["host"] = parts.hostname or sock_dir or "localhost"
        kwargs["port"] = port
    if "application_name" in q:
        kwargs["application_name"] = q["application_name"][0]
    return kwargs


def open_backend(url: str) -> SqlBackend:
    if url.startswith("sqlite:"):
        path = url.removeprefix("sqlite:///").removeprefix("sqlite://")
        return SQLiteBackend(Path(path) if path != ":memory:" else ":memory:")
    return PostgresBackend(url)


def ping(backend: SqlBackend) -> bool:
    with backend.transaction() as tx:
        return int(tx.execute("SELECT 1")[0][0]) == 1


__all__ = ["PostgresBackend", "connect_kwargs", "open_backend", "ping"]
