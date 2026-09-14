"""Snapshot and incremental checkpoints; never writes legacy engine run trees."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path


class RunBusy(RuntimeError):
    """Another process owns this output directory."""


def checkpoint_path(out) -> Path:
    return Path(out) / "browse_run.json"


def read_checkpoint(out) -> dict:
    with checkpoint_path(out).open(encoding="utf-8") as stream:
        state = json.load(stream)
    from flowmirror.platform.browse_journal import FORMAT, JournalFrames
    if isinstance(state, dict) and state.get('format') == FORMAT:
        return JournalFrames(out).load(state)
    if not isinstance(state, dict) or state.get("format") != "flowmirror-browse-v1":
        raise ValueError("unsupported browsing checkpoint")
    return state


def save_checkpoint(out, state):
    """Called only by the writer-lock owner; readers see old or new whole JSON."""
    from flowmirror.platform.browse_journal import JournalFrames
    if isinstance(state.get('frames'), JournalFrames):
        state['frames'].save(state)
        return
    path = checkpoint_path(out)
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(state, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def checkpoint_version(out):
    """Observer cache key; includes append-only events, not just the header."""
    from flowmirror.platform.browse_journal import event_path
    paths = (checkpoint_path(out), event_path(out))
    return tuple((p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None for p in paths)


@contextmanager
def writer_lock(out):
    """OS-owned lock: crashes release ownership, never delete a lock another owns."""
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=True)
    # The lock byte need not contain a PID and has no stale-file semantics.
    with (directory / ".writer.lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RunBusy("browsing output is already in use") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
