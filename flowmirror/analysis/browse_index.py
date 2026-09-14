"""Rebuildable, observer-only cache for completed journal runs. Never a replay input."""
from __future__ import annotations

import argparse
import copy
import json
from collections.abc import Sequence
from pathlib import Path
import time

from flowmirror.platform.browse_store import checkpoint_path, checkpoint_version, read_checkpoint
from flowmirror.platform.browse_journal import FORMAT as JOURNAL_FORMAT, JournalFrames, event_path

FORMAT = 'flowmirror-browse-observer-index-v1'


class IndexUnavailable(ValueError):
    """A derived cache is absent, stale or inconsistent; read the source instead."""


def source_version(out):
    return [list(item) if item is not None else None for item in checkpoint_version(out)]


def build_index(out, destination):
    """Read the original journal, write a NEW derived file outside its run tree."""
    out, destination = Path(out).resolve(), Path(destination).resolve()
    if destination.is_relative_to(out):
        raise ValueError('observer index must be outside the source run directory')
    if destination.exists():
        raise FileExistsError('index exists; choose a new derived output path')
    before = source_version(out)
    started = time.perf_counter()
    state = read_checkpoint(out)
    frames = state['frames']
    if (not isinstance(frames, JournalFrames) or state['status'] != 'completed'
            or state.get('pending') is not None or frames.committed_bytes != frames.path.stat().st_size):
        raise ValueError('indexing requires a clean completed journal; other runs use the normal reader')
    from flowmirror.analysis.browse_observer import summarize_browse
    payload = {'format': FORMAT, 'source_run': str(out), 'source_version': before,
               'committed_bytes': frames.committed_bytes, 'final_commit_offset': frames.last_commit_offset,
               'offsets': frames.offsets,
               'sessions': [[actor, phase, indices] for (actor, phase), indices in frames.sessions.items()],
               'snapshot_locations': frames.snapshot_locations, 'summary': summarize_browse(state)}
    if source_version(out) != before:
        raise ValueError('source changed during indexing; no cache written')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        stream.write('\n')
    return {'index': str(destination), 'frames': len(frames), 'sessions': len(frames.sessions),
            'index_bytes': destination.stat().st_size, 'build_seconds': time.perf_counter() - started,
            'notice': 'Derived observer cache only; replay always reads original records.'}


class _Source:
    def __init__(self, out, data):
        self.out = Path(out).resolve()
        self.path = event_path(out)
        self.version = data['source_version']
        if data['source_run'] != str(self.out) or data.get('format') != FORMAT:
            raise IndexUnavailable('index belongs to a different source or format')
        self.check()
        with checkpoint_path(out).open(encoding='utf-8') as stream:
            self.header = json.load(stream)
        if self.header.get('format') != JOURNAL_FORMAT:
            raise IndexUnavailable('not a journal source')
        self.limit = data['committed_bytes']
        if type(self.limit) is not int or self.limit != self.path.stat().st_size:
            raise IndexUnavailable('index does not cover the completed source')
        final, end = self.record(data['final_commit_offset'])
        if (end != self.limit or final.get('kind') != 'commit' or final.get('status') != 'completed'
                or final.get('pending') is not None):
            raise IndexUnavailable('source is not a clean completed journal')
        self.final = final

    def check(self):
        if source_version(self.out) != self.version:
            raise IndexUnavailable('source changed after indexing')

    def record(self, offset):
        if type(offset) is not int or not 0 <= offset < self.limit:
            raise IndexUnavailable('invalid source offset')
        with self.path.open('rb') as stream:
            stream.seek(offset)
            line = stream.readline(self.limit - offset)
            end = stream.tell()
        if not line.endswith(b'\n'):
            raise IndexUnavailable('incomplete indexed record')
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            raise IndexUnavailable('invalid indexed record') from exc
        if not isinstance(value, dict):
            raise IndexUnavailable('invalid indexed record')
        return value, end


class _Frames:
    def __init__(self, source, data):
        self.source, self.offsets = source, data['offsets']
        self.sessions = {}
        agents = {a['id'] for a in source.header['spec']['agents']}
        phases = source.header['spec']['phases']
        count = source.final['frame_count']
        if not isinstance(self.offsets, list) or len(self.offsets) != count:
            raise IndexUnavailable('frame index length differs')
        previous = -1
        for offset in self.offsets:
            if type(offset) is not int or not previous < offset < source.limit:
                raise IndexUnavailable('invalid frame index')
            previous = offset
        used = bytearray(count)
        for actor, phase, indices in data['sessions']:
            key = (actor, phase)
            if (actor not in agents or type(phase) is not int or not 0 <= phase < phases
                    or key in self.sessions or not isinstance(indices, list) or not indices):
                raise IndexUnavailable('invalid session index')
            previous = -1
            for index in indices:
                if type(index) is not int or not previous < index < count or used[index]:
                    raise IndexUnavailable('invalid session frame index')
                used[index] = 1
                previous = index
            self.sessions[key] = indices
        if len(self.sessions) != len(agents) * phases or not all(used):
            # Empty feeds (including all-wait or later-release marketing runs)
            # can legitimately have no frames for some sessions. Keep the source
            # reader fallback: the cache alone cannot distinguish those sessions
            # from a tampered omission, and marketing is not a coverage exemption.
            raise IndexUnavailable('incomplete completed-run session index')

    def __len__(self):
        return len(self.offsets)

    def select(self, actor, phase):
        self.source.check()
        result = []
        for index in self.sessions.get((actor, phase), []):
            row, _ = self.source.record(self.offsets[index])
            frame = row.get('value', {})
            if (row.get('kind') != 'frame' or frame.get('index') != index
                    or frame.get('agent_id') != actor or frame.get('phase') != phase):
                raise IndexUnavailable('indexed frame identity differs from actual source')
            result.append(frame)
        self.source.check()
        return result


class _Snapshots(Sequence):
    def __init__(self, source, key, locations):
        self.source, self.key, self.locations = source, key, locations
        expected = source.header['spec']['phases'] + (0 if key == 'institution_snapshots' else 1)
        if not isinstance(locations, list) or len(locations) != expected:
            raise IndexUnavailable('incomplete snapshot index')
        for offset, index in locations:
            if type(index) is not int or index < 0 or (offset is not None and
                    (type(offset) is not int or not 0 <= offset < source.limit)):
                raise IndexUnavailable('invalid snapshot index')

    def __len__(self):
        return len(self.locations)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        self.source.check()
        offset, item = self.locations[index]
        try:
            if offset is None:
                value = copy.deepcopy(self.source.header[self.key][item])
                actual_index = item
            else:
                record, _ = self.source.record(offset)
                if record.get('kind') != 'commit':
                    raise IndexUnavailable('snapshot location is not a commit')
                delta = record[self.key]
                value = delta['items'][item]
                actual_index = delta['from'] + item
            if actual_index != index or (self.key == 'snapshots' and value.get('epoch') != index):
                raise IndexUnavailable('snapshot belongs to a different phase')
            if self.key == 'institution_snapshots' and value.get('phase') != index:
                raise IndexUnavailable('institution snapshot belongs to a different phase')
        except (KeyError, IndexError, TypeError) as exc:
            raise IndexUnavailable('invalid indexed snapshot') from exc
        self.source.check()
        return value


def load_index(out, index_path):
    """Return an optional fast projection, never a state accepted by the runner."""
    try:
        with Path(index_path).open(encoding='utf-8') as stream:
            data = json.load(stream)
        source = _Source(out, data)
        frames = _Frames(source, data)
        summary = data['summary']
        spec = source.header['spec']
        if (not isinstance(summary, dict) or summary.get('frames') != len(frames)
                or summary.get('status') != 'completed' or summary.get('pending') is not False
                or summary.get('policy_calls') != source.final['policy_calls']
                or summary.get('policy_mode') != spec['policy_mode']
                or summary.get('model_calls') != (None if spec['policy_mode'] == 'external' else 0)
                or summary.get('phases') != spec['phases']
                or summary.get('completed_phases') != spec['phases']
                or summary.get('phase_times') != spec.get('phase_times')
                or summary.get('agents') != [{k: a[k] for k in ('id', 'handle', 'arm')} for a in spec['agents']]):
            raise IndexUnavailable('summary does not match source control')
        locations = data['snapshot_locations']
        state = {'spec': source.header['spec'], 'frames': frames,
                 'snapshots': _Snapshots(source, 'snapshots', locations['snapshots'])}
        if 'trading' in state['spec']:
            state['account_snapshots'] = _Snapshots(source, 'account_snapshots', locations['account_snapshots'])
        if 'marketing' in state['spec']:
            state['institution_snapshots'] = _Snapshots(source, 'institution_snapshots', locations['institution_snapshots'])
        source.check()
        return state, summary
    except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError):
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(build_index(args.run, args.out), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
