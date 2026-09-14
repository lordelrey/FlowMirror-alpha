"""Measure a named, strictly offline warehouse probe; never creates an LLM client."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

CODE_ROOT = Path(__file__).resolve().parent.parent
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from flowmirror.platform.browse_run import run_browsing, replay_run
from flowmirror.platform.browse_store import read_checkpoint
from flowmirror.platform.warehouse import warehouse_policy


def process_peak_bytes():
    """Process peak working set on Windows; peak RSS on Unix, no extra package."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('peak_working_set', 'working_set',
                    'peak_paged', 'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        counters = Counters()
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), ctypes.sizeof(counters)):
            return None
        return counters.peak_working_set
    import resource
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == 'darwin' else value * 1024)


def compare_recorded(left, right):
    """Exact records, not a digest and not a policy rerun."""
    a, b = read_checkpoint(left), read_checkpoint(right)
    if {k: v for k, v in a.items() if k != 'frames'} != {k: v for k, v in b.items() if k != 'frames'}:
        raise ValueError('saved non-frame state differs')
    if len(a['frames']) != len(b['frames']):
        raise ValueError('saved frame counts differ')
    for index, (fa, fb) in enumerate(zip(a['frames'], b['frames'])):
        if fa != fb:
            raise ValueError('saved frame differs at ' + str(index))
    return {'identical': True, 'frames_compared': len(a['frames'])}


def measure(spec_path, out, *, max_new_calls, storage, compare_run=None):
    with Path(spec_path).open(encoding='utf-8') as stream:
        spec = json.load(stream)
    if (spec.get('policy_mode'), spec.get('policy_name')) != ('scripted', 'warehouse-open-finish-v1'):
        raise ValueError('measurement accepts only the named offline warehouse policy')
    started = time.perf_counter()
    result = run_browsing(spec, out, warehouse_policy, max_new_calls=max_new_calls, storage=storage)
    elapsed = time.perf_counter() - started
    peak = process_peak_bytes()  # before replay or loading a legacy comparison
    record = {'scope': 'offline storage/capacity measurement; not model or human behavior',
              'out': str(Path(out).resolve()), 'requested_storage_for_new_run': storage,
              'agents': len(spec['agents']), 'phases': spec['phases'], 'execution': result,
              'wall_seconds': elapsed, 'run_peak_working_set_or_rss_bytes': peak,
              'files_bytes': {p.name: p.stat().st_size for p in Path(out).iterdir() if p.is_file()},
              'source_spec': str(Path(spec_path).resolve()), 'corpus_info': spec.get('corpus_info')}
    print(json.dumps({'run_finished': record}, ensure_ascii=False), flush=True)
    started = time.perf_counter()
    record['replay'] = replay_run(out)
    record['replay_seconds'] = time.perf_counter() - started
    if compare_run:
        started = time.perf_counter()
        record['comparison'] = compare_recorded(out, compare_run)
        record['comparison_seconds'] = time.perf_counter() - started
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--max-new-calls', type=int, default=600)
    parser.add_argument('--storage', choices=['snapshot', 'journal'], default='journal')
    parser.add_argument('--compare-run', type=Path)
    args = parser.parse_args(argv)
    if args.report.exists():
        parser.error('report already exists; supply a new report path')
    record = measure(args.spec, args.out, max_new_calls=args.max_new_calls,
                     storage=args.storage, compare_run=args.compare_run)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps(record, ensure_ascii=False), flush=True)
    return 0 if record['replay']['identical'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
