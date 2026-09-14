"""Read-only source/index parity and observer latency measurement; no model calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import sys
import time

CODE_ROOT = Path(__file__).resolve().parent.parent
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from flowmirror.analysis.browse_observer import BrowseObserver
from flowmirror.platform.browse_store import checkpoint_version


def measure(run, index):
    version = checkpoint_version(run)
    started = time.perf_counter()
    fast = BrowseObserver(run, index)
    if not fast.indexed:
        raise ValueError('index unavailable; latency must not be mislabeled as indexed')
    summary = fast.summary()
    indexed_summary_seconds = time.perf_counter() - started
    started = time.perf_counter()
    normal = BrowseObserver(run)
    normal_summary = normal.summary()
    normal_summary_seconds = time.perf_counter() - started
    if summary != normal_summary:
        raise ValueError('derived summary differs from the original-record projection')
    cached, original = fast.state['frames'], normal.state['frames']
    if cached.offsets != original.offsets or cached.sessions != original.sessions:
        raise ValueError('frame/session pointers differ from full source scan')
    for key, locations in original.snapshot_locations.items():
        if fast.state[key].locations != [list(item) for item in locations]:
            raise ValueError('snapshot pointers differ from full source scan')
    pairs = list(original.sessions)
    sample = random.Random(2027).sample(pairs, min(30, len(pairs)))
    sample = list(dict.fromkeys([pairs[0], pairs[-1], *sample]))
    latencies = []
    for actor, phase in sample:
        expected = normal.agent_trace(actor, phase)
        started = time.perf_counter()
        actual = fast.agent_trace(actor, phase)
        latencies.append(time.perf_counter() - started)
        if not fast.indexed or actual != expected:
            raise ValueError('indexed source trace differs')
    if checkpoint_version(run) != version:
        raise ValueError('source changed during measurement')
    return {'run': str(Path(run).resolve()), 'index': str(Path(index).resolve()),
            'frames': len(original), 'sessions': len(pairs), 'summary_equal': True,
            'all_offsets_sessions_and_snapshot_locations_equal': True,
            'sampled_traces_equal': len(sample), 'sample_seed': 2027,
            'indexed_summary_seconds': indexed_summary_seconds,
            'normal_summary_seconds': normal_summary_seconds,
            'selected_trace_seconds_min': min(latencies), 'selected_trace_seconds_max': max(latencies),
            'source_version_unchanged': True, 'model_calls': 0,
            'notice': 'One local process; first observer construction, not a cold OS page-cache benchmark. Full replay remains separate.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--index', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args(argv)
    if args.report.exists():
        parser.error('report exists; select a new output path')
    result = measure(args.run, args.index)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write('\n')
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
