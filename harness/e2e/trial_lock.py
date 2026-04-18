"""Per-trial advisory lock using fcntl.flock.

Ensures only one run_e2e process owns a (workspace_root, trial_name) pair
at a time. The lock is released automatically by the kernel when the holder's
file descriptor closes (process exit, SIGKILL, OOM all release cleanly), so
no atexit cleanup is required.

Layout:
    <workspace_root>/e2e_trial/.locks/<trial_name>.lock   # fcntl.flock target
    <workspace_root>/e2e_trial/.locks/<trial_name>.info   # JSON sidecar (diagnostic only)

Lock files live outside the trial dir on purpose so that --force rmtree on
the trial dir does not race with our own lock.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import signal
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class LockHandle:
    """Holds an acquired trial lock. Keep alive for the lock to persist."""

    fd: int
    lock_path: Path
    info_path: Path


def _locks_dir(workspace_root: Path) -> Path:
    return workspace_root / "e2e_trial" / ".locks"


def _lock_path_for(workspace_root: Path, trial_name: str) -> Path:
    return _locks_dir(workspace_root) / f"{trial_name}.lock"


def _info_path_for(workspace_root: Path, trial_name: str) -> Path:
    return _locks_dir(workspace_root) / f"{trial_name}.info"


def read_owner_info(workspace_root: Path, trial_name: str) -> Optional[dict]:
    """Read sidecar info for the most recent lock owner (may be stale)."""
    try:
        return json.loads(_info_path_for(workspace_root, trial_name).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.rename(str(tmp), str(path))


def _kill_gracefully(pid: int, term_wait_s: float = 10.0) -> bool:
    """SIGTERM, wait up to term_wait_s, then SIGKILL. Returns True when gone."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True

    deadline = time.time() + term_wait_s
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True

    time.sleep(1.0)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _format_owner(owner: Optional[dict]) -> str:
    if not owner:
        return "  (no sidecar info found — owner may have crashed mid-acquire)"
    return (
        f"  PID:        {owner.get('pid')}\n"
        f"  Started:    {owner.get('started_at')}\n"
        f"  Cmdline:    {owner.get('cmdline')}\n"
        f"  Host:       {owner.get('host')}"
    )


def acquire_trial_lock(
    workspace_root: Path,
    trial_name: str,
    force: bool = False,
) -> LockHandle:
    """Acquire exclusive lock for (workspace_root, trial_name).

    On busy + force=False: print owner info to stderr, sys.exit(1).
    On busy + force=True: SIGTERM the old PID (then SIGKILL after 10s), retry.

    Returns a LockHandle that the caller MUST keep alive (assign to a local
    or module variable). The lock auto-releases when the fd closes.
    """
    lock_path = _lock_path_for(workspace_root, trial_name)
    info_path = _info_path_for(workspace_root, trial_name)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        owner = read_owner_info(workspace_root, trial_name)

        if not force:
            print(
                f"Trial '{trial_name}' is already owned by another run_e2e process.\n"
                f"{_format_owner(owner)}\n"
                f"Use --force to take over (SIGTERM, then SIGKILL after 10s).",
                file=sys.stderr,
            )
            os.close(fd)
            sys.exit(1)

        if not owner or "pid" not in owner:
            print(
                f"Lock {lock_path} is held but no PID in sidecar; cannot --force safely. "
                f"Investigate manually.",
                file=sys.stderr,
            )
            os.close(fd)
            sys.exit(1)

        old_pid = int(owner["pid"])
        if old_pid == os.getpid():
            print(f"Lock somehow held by self (PID {old_pid}); bailing.", file=sys.stderr)
            os.close(fd)
            sys.exit(1)

        logger.warning(
            f"--force: terminating stale lock owner PID {old_pid} "
            f"(SIGTERM, then SIGKILL after 10s if still alive)"
        )
        if not _kill_gracefully(old_pid):
            print(f"Failed to terminate stale owner PID {old_pid}.", file=sys.stderr)
            os.close(fd)
            sys.exit(1)

        # Kernel may need a tick to release the fd from the dead process.
        for _ in range(20):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.5)
        else:
            print(
                f"Could not acquire lock {lock_path} even after killing PID {old_pid}.",
                file=sys.stderr,
            )
            os.close(fd)
            sys.exit(1)

        logger.info(f"--force: took over lock from PID {old_pid}")

    # We hold the lock — write a fresh sidecar.
    _atomic_write_json(
        info_path,
        {
            "pid": os.getpid(),
            "started_at": datetime.utcnow().isoformat() + "Z",
            "cmdline": " ".join(sys.argv),
            "host": socket.gethostname(),
            "trial_name": trial_name,
        },
    )

    return LockHandle(fd=fd, lock_path=lock_path, info_path=info_path)
