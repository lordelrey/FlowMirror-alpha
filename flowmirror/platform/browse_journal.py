"""Incremental browsing storage; private local files, not a source database.

The header stores the initial state once. JSONL frame records are visible only
after a complete commit record. The writer fsyncs that commit before returning
to the runner, preserving its existing pending/response side-effect ordering.
Readers never repair files; only a writer-lock owner may back up an unfinished
tail and resume from the last committed byte. Old snapshot runs stay unchanged.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path


FORMAT = 'flowmirror-browse-journal-v1'
EVENTS = 'browse_events.jsonl'


def event_path(out):
    root = Path(out).resolve()
    path = root / EVENTS
    if path.resolve().parent != root:
        raise ValueError('browsing journal escapes its run directory')
    return path


def _encode(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False,
                       separators=(',', ':')) + '\n').encode('utf-8')


class JournalFrames(Sequence):
    """Disk-backed committed frames plus at most one offline checkpoint batch.

    The offset/session index is reconstructed in memory, not a second persisted
    authority. No image hashes, database migrations or hidden policy inputs.
    """
    def __init__(self, out):
        self.path = event_path(out)
        self.offsets = []
        self.sessions = {}
        self.buffer = []
        self.committed_bytes = 0
        self._snapshot_tails = {}
        self.snapshot_locations = {}
        self.last_commit_offset = None

    def __len__(self):
        return len(self.offsets) + len(self.buffer)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        if index >= len(self.offsets):
            return copy.deepcopy(self.buffer[index - len(self.offsets)])
        with self.path.open('rb') as stream:
            return self._read_at(stream, self.offsets[index])

    def __iter__(self):
        if self.offsets:
            with self.path.open('rb') as stream:
                for offset in self.offsets:
                    yield self._read_at(stream, offset)
        for frame in self.buffer:
            yield copy.deepcopy(frame)

    @staticmethod
    def _read_at(stream, offset):
        stream.seek(offset)
        record = json.loads(stream.readline())
        if record.get('kind') != 'frame':
            raise ValueError('journal frame offset does not point to a frame')
        return record['value']

    def select(self, agent_id, phase):
        indices = self.sessions.get((agent_id, phase), [])
        if indices:
            with self.path.open('rb') as stream:
                for index in indices:
                    yield self._read_at(stream, self.offsets[index])
        for frame in self.buffer:
            if frame['agent_id'] == agent_id and frame['phase'] == phase:
                yield copy.deepcopy(frame)

    def append(self, frame):
        if frame.get('index') != len(self):
            raise ValueError('nonsequential journal frame')
        self.buffer.append(frame)

    def _remember(self, rows):
        for offset, actor, phase in rows:
            self.sessions.setdefault((actor, phase), []).append(len(self.offsets))
            self.offsets.append(offset)

    def load(self, initial):
        state = copy.deepcopy(initial)
        state['format'] = 'flowmirror-browse-v1'
        state['frames'] = self
        self.snapshot_locations = {key: [(None, i) for i in range(len(state[key]))]
                                   for key in ('snapshots', 'account_snapshots', 'institution_snapshots') if key in state}
        pending_rows = []
        if self.path.exists():
            with self.path.open('rb') as stream:
                while True:
                    offset = stream.tell()
                    line = stream.readline()
                    if not line or not line.endswith(b'\n'):
                        break  # a partial final line is not committed
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError('invalid journal record')
                    if record.get('kind') == 'frame':
                        frame = record['value']
                        if frame.get('index') != len(self.offsets) + len(pending_rows):
                            raise ValueError('nonsequential journal frame')
                        pending_rows.append((offset, frame['agent_id'], frame['phase']))
                    elif record.get('kind') == 'commit':
                        if record.get('frame_count') != len(self.offsets) + len(pending_rows):
                            raise ValueError('journal commit frame count differs')
                        for key in ('snapshots', 'account_snapshots', 'institution_snapshots'):
                            if key not in record:
                                continue
                            delta = record[key]
                            start, items = delta['from'], delta['items']
                            previous = state.get(key, [])
                            if type(start) is not int or not 0 <= start <= len(previous) or not isinstance(items, list):
                                raise ValueError('invalid journal snapshot delta')
                            state[key] = previous[:start] + items
                            self.snapshot_locations[key] = self.snapshot_locations.get(key, [])[:start] + [
                                (offset, i) for i in range(len(items))]
                        for key in ('status', 'pending', 'policy_calls'):
                            state[key] = record[key]
                        self._remember(pending_rows)
                        pending_rows = []
                        self.committed_bytes = stream.tell()
                        self.last_commit_offset = offset
                    else:
                        raise ValueError('unknown journal record')
        self._remember_snapshot_tails(state)
        return state

    def _remember_snapshot_tails(self, state):
        for key in ('snapshots', 'account_snapshots', 'institution_snapshots'):
            if key in state:
                values = state[key]
                self._snapshot_tails[key] = (len(values), copy.deepcopy(values[-1]) if values else None)

    def repair_uncommitted_tail(self):
        """Writer-lock owner only. Preserve every removed byte in a new backup."""
        if not self.path.exists() or self.path.stat().st_size == self.committed_bytes:
            return None
        if self.path.stat().st_size < self.committed_bytes:
            raise ValueError('committed journal bytes are missing')
        fd, backup = tempfile.mkstemp(prefix='browse_events.uncommitted.', suffix='.jsonl', dir=self.path.parent)
        with self.path.open('rb') as source, os.fdopen(fd, 'wb') as dest:
            source.seek(self.committed_bytes)
            shutil.copyfileobj(source, dest)
            dest.flush()
            os.fsync(dest.fileno())
        with self.path.open('r+b') as stream:
            stream.truncate(self.committed_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        return Path(backup)

    def save(self, state):
        """Append a batch, then atomically expose it with a durable commit line."""
        # No retries on I/O errors. A later lock owner can recover from the last
        # committed prefix, including a cached response not yet applied.
        control = {'kind': 'commit', 'frame_count': len(self),
                   **{k: state[k] for k in ('status', 'pending', 'policy_calls')}}
        for key in ('snapshots', 'account_snapshots', 'institution_snapshots'):
            if key not in state:
                continue
            values = state[key]
            old_count, old_tail = self._snapshot_tails.get(key, (0, None))
            if len(values) < old_count:
                raise ValueError('snapshot history cannot shrink')
            if len(values) == old_count and (not values or values[-1] == old_tail):
                continue
            start = max(0, old_count - 1)
            control[key] = {'from': start, 'items': values[start:]}
        # Encode before touching the log: invalid JSON cannot leave half a batch.
        frames = [_encode({'kind': 'frame', 'value': f}) for f in self.buffer]
        commit = _encode(control)
        expected = self.committed_bytes
        rows = []
        with self.path.open('ab') as stream:
            if stream.tell() != expected:
                raise ValueError('uncommitted tail requires writer recovery')
            for frame, encoded in zip(self.buffer, frames):
                rows.append((stream.tell(), frame['agent_id'], frame['phase']))
                stream.write(encoded)
            stream.write(commit)
            stream.flush()
            os.fsync(stream.fileno())
            self.committed_bytes = stream.tell()
        self._remember(rows)
        self.buffer.clear()
        self._remember_snapshot_tails(state)


def start_journal(out, state, write_header):
    path = event_path(out)
    if path.exists():
        raise ValueError('existing journal without header; use another output directory')
    header = {k: v for k, v in state.items() if k != 'frames'}
    header['format'] = FORMAT
    write_header(out, header)
    frames = JournalFrames(out)
    frames._remember_snapshot_tails(state)
    state['frames'] = frames
    return state
