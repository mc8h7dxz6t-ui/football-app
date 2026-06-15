"""Cross-process exclusive lock for hibs-racing feature_store.sqlite writers."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Union

PathLike = Union[str, Path]


class FeatureStoreLockTimeout(RuntimeError):
    """Could not acquire the feature_store flock within the wait window."""


def resolve_feature_store_lock_path(db_path: PathLike) -> Path:
    """Lock file colocated with the database (override via env)."""
    override = os.environ.get("HIBS_RACING_FEATURE_STORE_LOCK", "").strip()
    if override:
        return Path(override)
    db = Path(db_path)
    return db.parent / f"{db.name}.lock"


def default_lock_wait_sec() -> float:
    raw = os.environ.get("RACING_FEATURE_STORE_LOCK_WAIT_SEC", "60")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 60.0


def lock_wait_enabled() -> bool:
    return os.environ.get("RACING_FEATURE_STORE_LOCK_NO_WAIT", "").strip().lower() not in (
        "1",
        "true",
        "yes",
    )


@contextmanager
def feature_store_lock(
    db_path: PathLike,
    *,
    wait: bool | None = None,
    timeout_sec: float | None = None,
) -> Iterator[Path]:
    """POSIX flock shared by verification settlement and hibs-racing daily refresh.

    Writers (settlement batch, card ingest) should hold this lock for the duration
    of their SQLite write transaction. Readers use WAL + read-only URIs and do not
    need this lock.
    """
    import fcntl

    if wait is None:
        wait = lock_wait_enabled()
    if timeout_sec is None:
        timeout_sec = default_lock_wait_sec()

    lock_path = resolve_feature_store_lock_path(db_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("a+", encoding="utf-8")
    deadline = time.monotonic() + timeout_sec

    acquired = False
    while not acquired:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            if not wait:
                fh.close()
                raise FeatureStoreLockTimeout(
                    f"feature_store locked: {lock_path} (wait disabled)"
                ) from None
            if time.monotonic() >= deadline:
                fh.close()
                raise FeatureStoreLockTimeout(
                    f"feature_store lock timeout after {timeout_sec}s: {lock_path}"
                ) from None
            time.sleep(0.25)

    try:
        fh.seek(0)
        fh.truncate()
        fh.write(datetime.now(timezone.utc).isoformat() + "\n")
        fh.flush()
        yield lock_path
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()
