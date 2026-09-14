"""Build and evaluate a private, offline calibration artifact with no providers.

Example: python -m flowmirror.analysis.calibration_run --corpus EXPORTED_CORPUS
  --out output/calibration_reports/NEW_TAG
  --train-until 2026-03-31T23:59:59+08:00
  --calibration-until 2026-04-30T23:59:59+08:00

The output must be new. The local observer only reads report.json; detailed
source-aligned rows and predictions are separate researcher artifacts.
"""
from __future__ import annotations

import argparse
from datetime import datetime, time, timezone
import json
from pathlib import Path
from time import perf_counter

from flowmirror.analysis.engagement_alignment import build_alignment
from flowmirror.analysis.temporal_dataset import build_dataset, CN
from flowmirror.analysis.temporal_evaluation import evaluate
from flowmirror.platform.warehouse import write_json, write_lines

REFERENCES = [
    {'name':'TwinMarket: trading, social interaction and environment separation',
     'url':'https://github.com/FreedomIntelligence/TwinMarket'},
    {'name':'FinMycelium: multi-source financial event reconstruction',
     'url':'https://github.com/AgenticFinLab/FinMycelium'},
    {'name':'PyFi: layered financial image understanding evaluation',
     'url':'https://arxiv.org/abs/2512.14735'},
]


def run(corpus_root, out, *, train_until, calibration_until,
        start='2025-09-05', end='2026-09-05', max_gap_days=14, alpha=1.0, coverage=.9, seed=2027):
    """Generate explicit new artifacts; source files are only opened for reading."""
    out, corpus = Path(out), Path(corpus_root)
    out.mkdir(parents=True, exist_ok=False)
    began = perf_counter()
    source_names = ('posts.jsonl','nav.jsonl','engagement_snapshot.jsonl')
    before = {name: {'bytes': (corpus/name).stat().st_size,
                     'mtime_ns': (corpus/name).stat().st_mtime_ns} for name in source_names}
    panel = build_dataset(corpus,start=start,end=end,max_gap_days=max_gap_days)
    evaluation = evaluate(panel['rows'], train_until=train_until, calibration_until=calibration_until,
                          end=datetime.combine(datetime.fromisoformat(end).date(), time.max, CN).isoformat(),
                          alpha=alpha, coverage=coverage, seed=seed)
    engagement = build_alignment(corpus)
    write_lines(out/'dataset.jsonl',panel.pop('rows'))
    membership = {name: part.pop('row_ids') for name,part in evaluation['splits'].items()}
    write_json(out/'split_membership.json', membership)
    for name, model in evaluation['models'].items():
        write_lines(out/f'predictions_{name}.jsonl', model.pop('predictions'))
    for comparison in evaluation['paired_comparisons'].values():
        for stratum in comparison['strata'].values():
            stratum.pop('daily', None)
    write_lines(out/'engagement_intervals.jsonl',engagement.pop('intervals'))
    after = {name: {'bytes': (corpus/name).stat().st_size,
                    'mtime_ns': (corpus/name).stat().st_mtime_ns} for name in source_names}
    source_unchanged = before == after
    limitations = panel['limitations'] + [
        'This is a one-step observational prediction reference, not a learned action-conditioned financial world model.',
        'No model/provider calls, investment decisions, brokerage operations or live experiments are performed.',
        'Dates are a single exploratory holdout, not independent institution holdout or an unseen intervention.',
        'Counts of fund-observation rows must not be presented as numbers of independent people or experimental runs.',
    ]
    if not source_unchanged:
        limitations.append('Source file metadata changed during this build; the export was not stable. Recheck before interpreting results.')
    report = {'kind':'offline_temporal_calibration','label':'中国历史净值与发布活动：时间留出参照',
              'created_at':datetime.now(timezone.utc).isoformat(), 'limitations':limitations,
              'nav':{'support':panel['support'],'settings':panel['settings'],'evaluation':evaluation},
              'engagement':engagement, 'reference_sources':REFERENCES}
    write_json(out/'report.json',report)
    receipt = {'status':evaluation['status'],'new_model_calls':0,'seconds':perf_counter()-began,
               'source_file_metadata_unchanged':source_unchanged,'source_before':before,'source_after':after,
               'files':sorted(p.name for p in out.iterdir())}
    write_json(out/'build_receipt.json',receipt)
    return {'receipt':receipt,'nav_support':panel['support'],
            'engagement_support':engagement['support'],
            'splits':{k:{f:v for f,v in part.items() if f in ('n_rows','n_units','n_target_days','n_publication_exposed')}
                      for k,part in evaluation['splits'].items()},
            'models':{k:v['metrics'] for k,v in evaluation['models'].items()},
            'publication_vs_state':evaluation['paired_comparisons']['publication_ridge_minus_state_ridge']}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--corpus',required=True)
    ap.add_argument('--out',required=True)
    ap.add_argument('--start',default='2025-09-05')
    ap.add_argument('--end',default='2026-09-05')
    ap.add_argument('--train-until',required=True)
    ap.add_argument('--calibration-until',required=True)
    ap.add_argument('--max-gap-days',type=int,default=14)
    ap.add_argument('--alpha',type=float,default=1.0)
    ap.add_argument('--coverage',type=float,default=.9)
    ap.add_argument('--seed',type=int,default=2027)
    args=vars(ap.parse_args())
    corpus=args.pop('corpus')
    print(json.dumps(run(corpus,**args),ensure_ascii=False,allow_nan=False,indent=2))


if __name__=='__main__':
    main()
