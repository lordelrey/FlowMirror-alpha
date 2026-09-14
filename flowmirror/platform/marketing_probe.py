"""Build private institution publication specs from existing paired source cards.

This module makes no model requests and starts no simulation. Source material is
reused verbatim; institution management and every resulting publication are
SIMULATED, not real campaigns or evidence about human behavior.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import json
import math
import random
from pathlib import Path

from flowmirror.market.tape import instant
from flowmirror.platform.browse_run import normalize_spec
from flowmirror.platform.matched_probe import build_probe
from flowmirror.platform.warehouse import lines, write_json, write_lines


NOTICE = (
    'Private research specification. Reused real source material; institution '
    'management and publication are SIMULATED. These are not real campaigns, '
    'observed human behavior, or evidence of causal marketing effects.'
)


def _integer(value, name, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return value


def _paired_groups(spec):
    groups = defaultdict(list)
    for actor in spec['agents']:
        group = actor.get('feed_group')
        _integer(group, 'paired feed_group', 0)
        groups[group].append(actor)
    for actors in groups.values():
        if len(actors) != 3 or {a['arm'] for a in actors} != {'T', 'TC', 'TV'}:
            raise ValueError('source population must have one T/TC/TV triplet per feed_group')
        for field in ('private_state', 'market', 'market_instruments', 'account'):
            if any(a.get(field) != actors[0].get(field) for a in actors[1:]):
                raise ValueError(f'source paired covariates differ: {field}')
    return [groups[g] for g in sorted(groups)]


def _population(spec, groups, seed):
    originals = _paired_groups(spec)
    report = {'source_agents': len(spec['agents']), 'source_groups': len(originals),
              'requested_groups': groups, 'resampled': groups is not None,
              'notice': 'Synthetic paired investors; copies do not add sourceposts or independent societies.'}
    if groups is not None:
        _integer(groups, 'groups')
        population, copies = [], []
        rng = random.Random(seed)
        for group in range(groups):
            source = originals[group % len(originals)]
            actors = []
            arms = ['T', 'TC', 'TV']
            rng.shuffle(arms)
            for original, arm in zip(sorted(source, key=lambda a: ('T', 'TC', 'TV').index(a['arm'])), arms):
                actor = copy.deepcopy(original)
                identity = f'investor_probe_{len(population):05d}'
                actor.update(id=identity, handle='@' + identity, arm=arm, feed_group=group)
                population.append(actor)
                actors.append(identity)
            copies.append({'feed_group': group, 'source_feed_group': source[0]['feed_group'],
                           'source_agents': [a['id'] for a in source], 'agents': actors})
        spec['agents'] = population
        # Reassign pairing without regenerating the source cash/risk/persona.
        spec['population_design'] = {
            'kind': 'matched-triplets-v1', 'seed': seed,
            'groups': [{'feed_group': c['feed_group'], 'agents': c['agents'][:]} for c in copies],
            'notice': 'Synthetic paired covariates, not observed humans or independent societies.',
        }
        report['copies'] = copies
    report.update(agents=len(spec['agents']), groups=len(spec['agents']) // 3,
                  arm_counts=dict(sorted(Counter(a['arm'] for a in spec['agents']).items())))
    return spec, report


def _eligible(card, clocks):
    """Require original organization, date, market, and the complete paired gallery."""
    if (card.get('synthetic') is True or card.get('publication_kind')
            or card.get('institution_id') or card.get('source_post_id')):
        return None, 'not_original_source_material'
    if not isinstance(card.get('org'), str) or not card['org'].strip():
        return None, 'missing_source_org'
    if card.get('market') not in ('CN', 'US'):
        return None, 'missing_source_market'
    if not isinstance(card.get('title'), str) or not card['title'].strip():
        return None, 'missing_source_title'
    if 'published_at' not in card:
        return None, 'missing_source_date'
    published = instant(card['published_at'])
    phase = next((i for i, clock in enumerate(clocks) if clock >= published), None)
    if phase is None:
        return None, 'source_date_after_last_phase'
    refs, descriptions = card.get('image_refs'), card.get('image_descriptions')
    if (not isinstance(refs, list) or not refs or not isinstance(descriptions, list)
            or len(refs) != len(descriptions)
            or any(not isinstance(r, str) or not r for r in refs)
            or len(set(refs)) != len(refs)
            or any(not isinstance(d, str) or not d.strip() for d in descriptions)):
        return None, 'missing_or_unpaired_gallery'
    return phase, None


def build_marketing_probe(source_spec, *, publication_budget, strategy='rotate',
                          publish_phases=None, institution_limit=None, groups=None,
                          max_steps=None, seed=None, calls_per_hour=None):
    """Return a normalized external spec and support report without writing files.

    An institution limit keeps the other eligible organizations organic. Cards
    without dated, paired source support are omitted from both exposure paths.
    ``publication_budget`` counts publications per institution, not currency.
    """
    _integer(publication_budget, 'publication_budget', 0)
    if institution_limit is not None:
        _integer(institution_limit, 'institution_limit')
    if strategy not in ('rotate', 'feedback_select'):
        raise ValueError('strategy must be rotate or feedback_select')
    if calls_per_hour is not None and (
            type(calls_per_hour) not in (int, float) or not math.isfinite(calls_per_hour)
            or calls_per_hour <= 0):
        raise ValueError('calls_per_hour must be positive and finite')
    spec = normalize_spec(source_spec)
    if 'marketing' in spec:
        raise ValueError('source-spec must be a browse source without existing marketing')
    if 'phase_times' not in spec:
        raise ValueError('dated source material requires phase_times')
    if 'trading' not in spec and any('account' in a for a in spec['agents']):
        raise ValueError('account trading requires existing source spec.trading; no products or fees are invented')
    if max_steps is not None:
        spec['max_steps'] = _integer(max_steps, 'max_steps', 2)
    if seed is None:
        seed = spec.get('population_design', {}).get('seed', 2027)
    if type(seed) is not int:
        raise ValueError('seed must be an integer')
    phases = spec['phases']
    schedule = list(range(phases)) if publish_phases is None else copy.deepcopy(publish_phases)
    if (not isinstance(schedule, list)
            or any(type(p) is not int or not 0 <= p < phases for p in schedule)
            or len(set(schedule)) != len(schedule)):
        raise ValueError('publish_phases must be distinct zero-based phases within the run')
    schedule.sort()
    clocks = [instant(t) for t in spec['phase_times']]
    by_org, exclusions, selections = defaultdict(list), Counter(), []
    source_count = len(spec['cards'])
    for card in spec['cards']:
        phase, reason = _eligible(card, clocks)
        if reason is not None:
            exclusions[reason] += 1
            selections.append({'post_id': card['post_id'], 'destination': 'excluded', 'reason': reason})
        else:
            by_org[card['org']].append((card, phase))
    orgs = sorted(by_org)
    if not orgs:
        raise ValueError('no eligible dated source organizations with complete paired galleries')
    selected_orgs = orgs if institution_limit is None else orgs[:institution_limit]
    selected_set = set(selected_orgs)
    institutions, institution_support, organic = [], [], []
    for ordinal, org in enumerate(orgs, 1):
        cards = sorted(by_org[org], key=lambda item: (instant(item[0]['published_at']), item[0]['post_id']))
        if org not in selected_set:
            organic.extend(c for c, _ in cards)
            selections.extend({'post_id': c['post_id'], 'org': org, 'destination': 'organic',
                               'available_phase': phase} for c, phase in cards)
            continue
        markets = {c['market'] for c, _ in cards}
        if len(markets) != 1:
            raise ValueError(f'source organization has multiple markets: {org!r}; cannot invent institution ownership')
        identity = f'institution_{ordinal:04d}'
        creatives = []
        for creative_number, (card, phase) in enumerate(cards, 1):
            creative_id = f'creative_{creative_number:06d}'
            # Keep the exact source card, including gallery order and provenance.
            # MarketingDesk strips inherited comments at publication time.
            creatives.append({'id': creative_id, 'available_phase': phase, 'card': copy.deepcopy(card)})
            selections.append({'post_id': card['post_id'], 'org': org, 'destination': 'creative',
                               'institution_id': identity, 'creative_id': creative_id,
                               'published_at': card['published_at'], 'available_phase': phase,
                               'images': len(card['image_refs'])})
        institutions.append({'id': identity, 'org': org, 'market': next(iter(markets)),
                             'strategy': strategy, 'publication_budget': publication_budget,
                             'publish_phases': copy.deepcopy(schedule), 'creatives': creatives})
        slots = sum(p >= min(phase for _, phase in cards) for p in schedule)
        institution_support.append({'id': identity, 'org': org, 'sourceposts': len(cards),
                                    'creatives': len(creatives), 'publication_budget': publication_budget,
                                    'maximum_publications': min(publication_budget, slots)})
    # Preserve source order for organic feed inputs as well as every card value.
    organic_ids = {c['post_id'] for c in organic}
    spec['cards'] = [c for c in spec['cards'] if c['post_id'] in organic_ids]
    spec['marketing'] = institutions
    spec, population = _population(spec, groups, seed)
    spec.update(name=f"{spec.get('name', 'paired-source')}-marketing-{len(institutions)}institutions-{len(spec['agents'])}agents",
                policy_mode='external', policy_name='json-browse-matched-v1',
                offline_checkpoint_interval=1, publication_notice=NOTICE, research_visibility='private')
    spec = normalize_spec(spec)
    availability = []
    for phase, at in enumerate(spec['phase_times']):
        owners = []
        for owner in institutions:
            starts = [c['available_phase'] for c in owner['creatives']]
            owners.append({'institution_id': owner['id'],
                           'available_creatives': sum(p <= phase for p in starts),
                           'newly_available_creatives': starts.count(phase),
                           'scheduled': phase in schedule})
        availability.append({'phase': phase, 'as_of': at, 'institutions': owners,
                             'available_creatives': sum(o['available_creatives'] for o in owners),
                             'newly_available_creatives': sum(o['newly_available_creatives'] for o in owners),
                             'institutions_with_available_creatives': sum(o['available_creatives'] > 0 for o in owners),
                             'publication_slots_upper_bound': sum(bool(o['available_creatives']) and o['scheduled']
                                                                  and publication_budget > 0 for o in owners)})
    creative_count = sum(len(o['creatives']) for o in institutions)
    max_calls = len(spec['agents']) * phases * spec['max_steps']
    source_refs = {r for o in institutions for c in o['creatives'] for r in c['card']['image_refs']}
    report = {
        'notice': NOTICE, 'research_visibility': 'private', 'publication_kind': 'SIMULATED',
        'material_kind': 'reused_real_source_cards', 'model_calls': 0,
        'sourceposts': source_count, 'eligible_sourceposts': sum(len(cards) for cards in by_org.values()),
        'selected_sourceposts': creative_count, 'creatives': creative_count,
        'eligible_orgs': len(orgs), 'selected_institutions': len(institutions),
        'eligible_org_names': orgs, 'selected_orgs': selected_orgs,
        'institution_limit': institution_limit, 'institution_limit_applied': len(institutions) < len(orgs),
        'unselected_orgs_retained_organic': [o for o in orgs if o not in selected_set],
        'organic_sourceposts': len(spec['cards']), 'removed_from_organic': creative_count,
        'excluded_sourceposts': dict(exclusions), 'selected_source_images': len(source_refs),
        'selections': selections, 'institutions': institution_support,
        'phase_availability': availability, 'population': population,
        'agents': len(spec['agents']), 'groups': population['groups'], 'phases': phases,
        'max_steps': spec['max_steps'], 'maximum_investor_calls': max_calls,
        'maximum_policy_calls': max_calls, 'maximum_institution_rule_decisions': len(institutions) * phases,
        'institution_decision_bound_includes_waits': True,
        'maximum_simulated_publications': sum(o['maximum_publications'] for o in institution_support),
        'publication_bound_notice': 'At most one publication per institution per phase; waits are valid. Bounds are not forecasts.',
        'trading': ('existing_source_spec_trading_preserved' if 'trading' in spec else 'disabled_no_spec_trading'),
        'sourcepost_scaling': 'Population scaling does not create or replicate sourceposts or creatives.',
    }
    if calls_per_hour is not None:
        report['elapsed_hours'] = {
            'upper_bound_under_assumption': max_calls / calls_per_hour,
            'assumed_minimum_sustained_calls_per_hour': calls_per_hour,
            'assumption': 'Aggregate investor throughput stays at least this rate for the bounded calls; no retries, downtime, or additional setup/IO/rule overhead. A rate cap alone is insufficient for an elapsed-time upper bound.',
            'is_forecast': False, 'institution_rule_decisions_are_model_calls': False,
        }
    return spec, report


def _copy_selected_index(path, spec):
    refs = {ref for card in spec['cards'] for ref in card['image_refs']}
    refs.update(ref for owner in spec['marketing'] for item in owner['creatives']
                for ref in item['card']['image_refs'])
    selected, seen = [], set()
    for row in lines(path):
        if row.get('asset_ref') not in refs:
            continue
        ref = row['asset_ref']
        if ref in seen:
            raise ValueError('duplicate selected asset_ref in supplied image index')
        if not isinstance(row.get('path'), str) or not row['path']:
            raise ValueError('selected image index row requires original path')
        seen.add(ref)
        selected.append(row)
    if seen != refs:
        raise ValueError(f'supplied image index is missing {len(refs - seen)} selected gallery references')
    return selected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--source-spec', type=Path, help='existing paired external_spec.json; no corpus import')
    inputs.add_argument('--corpus', type=Path, help='existing local exported corpus for matched_probe.build_probe')
    parser.add_argument('--pool', type=Path)
    parser.add_argument('--images-root', type=Path)
    parser.add_argument('--start')
    parser.add_argument('--days', type=int)
    parser.add_argument('--groups', type=int, help='explicit number of independent synthetic T/TC/TV triplet copies')
    parser.add_argument('--max-steps', type=int)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--strategy', choices=('rotate', 'feedback_select'), default='rotate')
    parser.add_argument('--publication-budget', type=int, required=True, help='publication count per institution, may be zero')
    parser.add_argument('--publish-phases', type=int, nargs='*', help='zero-based phases; omit for all, pass empty for all waits')
    parser.add_argument('--institution-limit', type=int, help='first N organizations in sorted source order; default all eligible')
    parser.add_argument('--image-index', '--source-image-index', dest='image_index', type=Path,
                        help='only this explicitly supplied original index is filtered and copied; no image scanning')
    parser.add_argument('--calls-per-hour', type=float, help='assumed minimum sustained aggregate investor throughput, not a forecast')
    parser.add_argument('--out', type=Path, required=True, help='new output directory; existing directories are never overwritten')
    args = parser.parse_args(argv)
    try:
        if args.out.exists():
            raise FileExistsError('output directory already exists; choose a new --out')
        if args.source_spec is not None:
            if any(v is not None for v in (args.pool, args.images_root, args.start, args.days)):
                raise ValueError('--source-spec retains source phase times; corpus/pool/date options cannot be combined')
            with args.source_spec.open(encoding='utf-8') as stream:
                source = json.load(stream)
            source_info = {'kind': 'existing_paired_spec', 'path': str(args.source_spec.resolve())}
        else:
            if any(v is None for v in (args.pool, args.images_root, args.start)):
                raise ValueError('--corpus requires --pool, --images-root and --start')
            source, _, imported = build_probe(
                args.corpus, args.pool, args.images_root, start=args.start,
                days=2 if args.days is None else args.days,
                groups=3 if args.groups is None else args.groups,
                max_steps=6 if args.max_steps is None else args.max_steps,
                seed=2027 if args.seed is None else args.seed)
            source_info = {'kind': 'matched_probe_build', 'corpus': str(args.corpus.resolve()),
                           'support': imported}
        spec, report = build_marketing_probe(
            source, publication_budget=args.publication_budget, strategy=args.strategy,
            publish_phases=args.publish_phases, institution_limit=args.institution_limit,
            groups=args.groups if args.source_spec is not None else None,
            max_steps=args.max_steps, seed=args.seed, calls_per_hour=args.calls_per_hour)
        report['source_input'] = source_info
        images = None if args.image_index is None else _copy_selected_index(args.image_index, spec)
        report['image_index'] = {'explicitly_supplied': images is not None,
                                 'copied_rows': 0 if images is None else len(images),
                                 'notice': 'Original selected index rows only. No pixels scanned, synthesized, copied, or downloaded.'}
        shadow = copy.deepcopy(spec)
        shadow.update(policy_mode='scripted', policy_name='warehouse-open-finish-v1')
        shadow['name'] += '-offline-shadow'
        shadow = normalize_spec(shadow)
        args.out.mkdir(parents=True, exist_ok=False)
        write_json(args.out / 'external_spec.json', spec)
        write_json(args.out / 'shadow_spec.json', shadow)
        if images is not None:
            write_lines(args.out / 'image_index.jsonl', images)
        write_json(args.out / 'support.json', report)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps({k: report[k] for k in (
        'sourceposts', 'selected_sourceposts', 'eligible_orgs', 'selected_institutions',
        'creatives', 'organic_sourceposts', 'agents', 'phases', 'maximum_investor_calls',
        'maximum_institution_rule_decisions', 'model_calls', 'notice')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
