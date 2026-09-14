"""Build paired, bounded browse specifications from existing local source material.

No source writes or model clients. The output is a new experiment, not a migration
of a capacity run. Descriptions are source-pool annotations, not human truth.
"""
from __future__ import annotations

import argparse
import copy
from collections import Counter
import json
from pathlib import Path
import random
import re

from flowmirror.platform.browse_run import normalize_spec
from flowmirror.platform.warehouse import build_spec, lines, write_json, write_lines, RASTER


def match_population(spec, *, seed=2027):
    """Equal starting covariates within each triplet, distinct mutable state."""
    spec = copy.deepcopy(spec)
    if len(spec['agents']) % 3:
        raise ValueError('matched triplets need a multiple of three agents')
    rng = random.Random(seed)
    groups = []
    for start in range(0, len(spec['agents']), 3):
        block = spec['agents'][start:start + 3]
        prototype = block[0]
        arms = ['T', 'TC', 'TV']
        rng.shuffle(arms)
        group = start // 3
        for actor, arm in zip(block, arms):
            actor['arm'], actor['feed_group'] = arm, group
            for field in ('private_state', 'market', 'market_instruments', 'account'):
                if field in prototype:
                    actor[field] = copy.deepcopy(prototype[field])
                else:
                    actor.pop(field, None)
        # The persona must not pretend that a different actor owns this ID.
        persona = f'Synthetic investor profile {group}; risk group {group % 5}; not a real user.'
        cash = (10000, 30000, 50000, 100000, 200000)[group % 5]
        for actor in block:
            actor['private_state'].update(persona=persona, cash=cash, risk_class=group % 5)
            if 'account' in actor:
                actor['account']['cash'] = cash
        groups.append({'feed_group': group, 'agents': [a['id'] for a in block]})
    spec['population_design'] = {'kind': 'matched-triplets-v1', 'seed': seed, 'groups': groups,
                                 'notice': 'Synthetic paired covariates, not observed humans or independent societies.'}
    return spec


def build_probe(corpus, pool, images_root, *, start, days=2, groups=3, max_steps=6, seed=2027):
    if type(groups) is not int or groups < 1 or type(max_steps) is not int or max_steps < 2:
        raise ValueError('groups must be positive and max_steps >= 2')
    images_root = Path(images_root).resolve(strict=True)
    spec = build_spec(corpus, start=start, days=days, agents=3 * groups, seed=seed)
    source = {}
    for row in lines(pool):
        note = row.get('note_id')
        if note:
            if note in source:
                raise ValueError('duplicate note_id in source pool')
            source[note] = row
    cards, image_index, selections = [], [], []
    omitted, quality = Counter(), Counter()
    for card in spec['cards']:
        note = card['post_id'].removeprefix('xhs_')
        row = source.get(note)
        reason = None
        paired = []
        if row is None:
            reason = 'no_source_pool_record'
        else:
            names, descriptions = row.get('image_ids'), row.get('image_caption_frozen')
            if (not isinstance(names, list) or not names or not isinstance(descriptions, list)
                    or len(names) != len(descriptions) or any(not isinstance(n, str) for n in names)
                    or len(set(names)) != len(names)
                    or any(not isinstance(d, str) or not d.strip() for d in descriptions)):
                reason = 'missing_or_unpaired_description'
            elif not re.fullmatch(r'[A-Za-z0-9_-]+', note):
                reason = 'unsupported_note_id'
            else:
                for i, (name, description) in enumerate(zip(names, descriptions)):
                    if not isinstance(name, str):
                        reason = 'invalid_image_name'; break
                    path = (images_root / name).resolve()
                    if not path.is_relative_to(images_root) or path.suffix.lower() not in RASTER:
                        reason = 'image_path_outside_root_or_non_raster'; break
                    if not path.is_file():
                        reason = 'missing_image_file'; break
                    paired.append({'asset_ref': f'asset_pool_{note}_{i}', 'path': str(path),
                                   'image_index': i, 'description': description})
        if reason:
            omitted[reason] += 1
            selections.append({'post_id': card['post_id'], 'selected': False, 'reason': reason})
            continue
        updated = copy.deepcopy(card)
        updated['image_refs'] = [p['asset_ref'] for p in paired]
        updated['image_descriptions'] = [p['description'] for p in paired]
        # Whole-note OCR may refer to images beyond this exact selected gallery.
        updated['ocr_text'] = ''
        updated['image_caption_frozen'] = '\n'.join(updated['image_descriptions'])
        cards.append(updated)
        image_index.extend({'asset_ref': p['asset_ref'], 'path': p['path']} for p in paired)
        meta = row.get('caption_meta') or {}
        flags = meta.get('leak_flags') or []
        quality['posts_with_source_caption_flags'] += bool(flags)
        quality['source_caption_flags'] += len(flags)
        selections.append({'post_id': card['post_id'], 'selected': True, 'image_count': len(paired),
                           'published_at': card['published_at'], 'caption_model': meta.get('model'),
                           'caption_generated_at': meta.get('generated_at'), 'source_caption_flags': flags,
                           'alignment': 'source_pool_image_ids_and_description_list_by_position'})
    if not cards:
        raise ValueError('no dated posts with a complete local paired image/description gallery')
    candidates = len(spec['cards'])
    spec['cards'] = cards
    spec = match_population(spec, seed=seed)
    spec.update(name=f'matched-cn-probe-{start}-{3 * groups}agents', max_steps=max_steps,
                policy_mode='external', policy_name='json-browse-matched-v1', offline_checkpoint_interval=1)
    spec['corpus_info'].update(
        selected_posts=len(cards), selected_images=len(image_index),
        dated_posts_per_day=dict(sorted(Counter(c['published_at'][:10] for c in cards).items())),
        agent_population='synthetic_matched_triplets_v1',
        candidate_posts_before_paired_support=candidates,
        modality_definition='T=post text; TC=post text plus paired image descriptions; TV=post text plus corresponding pixels',
        preview_definition='TC first-image description; TV first image; full gallery after open',
        alignment='Original source-pool list pairing; annotation fidelity still needs human review; no whole-note OCR',
    )
    report = {'candidate_posts': candidates, 'selected_posts': len(cards), 'selected_images': len(image_index),
              'excluded_posts': dict(omitted), 'caption_quality': dict(quality), 'selections': selections,
              'agents': groups * 3, 'groups': groups, 'phases': days, 'max_steps': max_steps,
              'maximum_policy_calls': 3 * groups * days * max_steps, 'model_calls': 0,
              'notice': 'Specification only. No provider request sent. No claim of causal validity or human behavior.'}
    return normalize_spec(spec), image_index, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', required=True)
    parser.add_argument('--pool', required=True)
    parser.add_argument('--images-root', required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--days', type=int, default=2)
    parser.add_argument('--groups', type=int, default=3)
    parser.add_argument('--max-steps', type=int, default=6)
    parser.add_argument('--seed', type=int, default=2027)
    args = parser.parse_args(argv)
    spec, images, report = build_probe(args.corpus, args.pool, args.images_root, start=args.start,
                                       days=args.days, groups=args.groups, max_steps=args.max_steps, seed=args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / 'external_spec.json', spec)
    shadow = copy.deepcopy(spec)
    shadow.update(policy_mode='scripted', policy_name='warehouse-open-finish-v1')
    shadow['name'] += '-offline-shadow'
    write_json(out / 'shadow_spec.json', shadow)
    write_lines(out / 'image_index.jsonl', images)
    write_json(out / 'support.json', report)
    print(json.dumps({k: v for k, v in report.items() if k != 'selections'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
