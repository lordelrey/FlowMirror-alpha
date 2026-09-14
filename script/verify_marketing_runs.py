"""Read-only integration measurements; writes one new private JSON result."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flowmirror.analysis.browse_observer import BrowseObserver
from flowmirror.analysis.marketing_behavior import build_report
from flowmirror.platform.browse_store import read_checkpoint


def inspect(run, index):
    state = read_checkpoint(run)
    plain, cached = BrowseObserver(run), BrowseObserver(run, index_path=index)
    if not cached.indexed or cached.summary() != plain.summary():
        raise AssertionError('indexed and original summaries differ')
    sessions = 0
    for actor in state['spec']['agents']:
        for phase in range(state['spec']['phases']):
            left, right = plain.agent_trace(actor['id'], phase), cached.agent_trace(actor['id'], phase)
            if left != right:
                raise AssertionError('indexed trace differs')
            for i, metrics in enumerate(right['behavior_timeline']):
                if metrics['observed_frames'] != i + 1:
                    raise AssertionError('behavior includes future microsteps')
            sessions += 1
    report = build_report(state)
    choices = [{'phase': s['phase'], 'institution_id': d['institution_id'],
                'creative_id': d['action'].get('creative_id'), 'kind': d['action']['kind'],
                'reason': d['action']['reason'], 'feedback_through_phase': d['before']['feedback_through_phase']}
               for s in state['institution_snapshots'] for d in s['decisions']]
    return {'run': str(run.resolve()), 'source_index_summary_identical': True,
            'source_index_sessions_identical': sessions, 'indexed': cached.indexed,
            'summary': plain.summary(), 'choices': choices, 'trading': report['trading'],
            'per_arm': report['per_arm'], 'model_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.run, args.index)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write('\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('summary', 'trading', 'choices', 'per_arm')}))


if __name__ == '__main__':
    main()
