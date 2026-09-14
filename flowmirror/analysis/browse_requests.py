"""Prepare and inspect requests from saved views, without a provider/client.

This is request construction evidence, not evidence that a model saw an image.
Redacted example messages are private researcher artifacts, never policy inputs.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter, defaultdict
import copy
import io
import json
from pathlib import Path

from flowmirror.platform.browse_assets import IndexedImages
from flowmirror.platform.browse_driver import JsonBrowsePolicy
from flowmirror.platform.browse_store import read_checkpoint, checkpoint_version
from flowmirror.platform.warehouse import write_json, write_lines
from flowmirror.market.tape import instant


def inspect_requests(run, index, roots, destination):
    run, destination = Path(run).resolve(), Path(destination).resolve()
    if destination.is_relative_to(run):
        raise ValueError('request reports must be outside the source run')
    if destination.exists():
        raise FileExistsError(destination)
    version = checkpoint_version(run)
    state = read_checkpoint(run)
    def no_provider(*args, **kwargs):
        raise AssertionError('request inspection must never call a provider')
    policy = JsonBrowsePolicy(no_provider, image_loader=IndexedImages(index, roots))
    from PIL import Image
    agents = {a['id']: a for a in state['spec']['agents']}
    arms, examples, starts = defaultdict(Counter), {}, {}
    invalid_images, missing_images, attachment_mismatches = [], [], []
    exposures, future_posts, future_quotes = defaultdict(lambda: defaultdict(set)), [], []
    for frame in state['frames']:
        agent = agents[frame['agent_id']]
        arm = agent['arm']
        view = frame['before']
        visible_cards = view['feed'] + ([view['detail']] if view['detail'] else [])
        for card in visible_cards:
            exposures[(frame['phase'], card['post_id'])][arm].add(agent['id'])
            if card.get('published_at') and view.get('sim_time') and instant(card['published_at']) > instant(view['sim_time']):
                future_posts.append({'frame': frame['index'], 'post_id': card['post_id']})
        for quote in view.get('market_snapshot', {}).get('quotes', []):
            if instant(quote['available_at']) > instant(view['sim_time']):
                future_quotes.append({'frame': frame['index'], 'instrument_id': quote['instrument_id']})
        messages, delivery = policy.prepare(frame['before'])
        stats = arms[arm]
        stats['requests_prepared'] += 1
        stats['image_parts'] += len(messages[1]['content']) - 1
        stats['text_characters'] += len(messages[0]['content']) + len(messages[1]['content'][0]['text'])
        unavailable = delivery.get('missing_refs', []) + delivery.get('missing_shas', [])
        if unavailable or delivery.get('missing_post_ids'):
            missing_images.append({'frame': frame['index'], 'refs': unavailable,
                                   'post_ids': delivery.get('missing_post_ids', [])})
        redacted = copy.deepcopy(messages)
        attachment_by_part = {item['part_index']: item for item in delivery.get('image_attachments', [])}
        for part, content in enumerate(messages[1]['content']):
            if content.get('type') != 'image_url':
                continue
            item = attachment_by_part.get(part, {'ref': None, 'sources': []})
            if not item['sources']:
                attachment_mismatches.append({'frame': frame['index'], 'part_index': part, 'reason': 'no_source_mapping'})
            uri = content['image_url']['url']
            decoded = base64.b64decode(uri.split(',', 1)[1], validate=True)
            image_info = {'media_type': uri.split(';')[0].removeprefix('data:'), 'decoded_bytes': len(decoded)}
            try:
                with Image.open(io.BytesIO(decoded)) as image:
                    image_info.update(width=image.width, height=image.height)
                    image.verify()
            except (OSError, ValueError, SyntaxError):
                invalid_images.append({'frame': frame['index'], 'ref': item['ref']})
            for source in item['sources']:
                cards = frame['before']['feed'] if source['surface'] == 'feed' else [frame['before']['detail']]
                card = next((c for c in cards if c and c['post_id'] == source['post_id']), None)
                refs = (([card['image_sha']] if card.get('image_sha') else []) + list(card.get('image_refs') or [])) if card else []
                refs = list(dict.fromkeys(refs))
                if source['image_index'] >= len(refs) or refs[source['image_index']] != item['ref']:
                    attachment_mismatches.append({'frame': frame['index'], 'ref': item['ref']})
            redacted[1]['content'][part] = {'type': 'image_metadata', 'ref': item['ref'], **image_info}
        key = (arm, bool(frame['before']['detail']))
        if key not in examples:
            examples[key] = {'frame': frame['index'], 'agent_id': agent['id'], 'arm': arm,
                             'surface': 'detail' if key[1] else 'preview',
                             'messages': redacted, 'delivery': delivery}
        if frame['step'] == 0:
            starts[(frame['phase'], agent['id'])] = frame['before']
    groups = state['spec'].get('population_design', {}).get('groups', [])
    mismatched_starts, matched_starts, absent_starts = [], 0, 0
    for phase in range(state['spec']['phases']):
        for group in groups:
            views = [starts.get((phase, a)) for a in group['agents']]
            if any(v is None for v in views):
                absent_starts += 1; continue
            # Own histories and social effects may diverge after autonomous choices.
            # Only initial covariates, market support and initial feed order are paired.
            projected = [{'private_state': v['private_state'], 'market_snapshot': v.get('market_snapshot'),
                          'posts': [p['post_id'] for p in v['feed']]} for v in views]
            if all(p == projected[0] for p in projected):
                matched_starts += 1
            else:
                mismatched_starts.append({'phase': phase, 'feed_group': group['feed_group']})
    report = {'run': str(run), 'prepared_frames': sum(s['requests_prepared'] for s in arms.values()),
              'by_arm': {k: dict(v) for k, v in sorted(arms.items())}, 'model_calls': policy.model_calls,
              'missing_images': missing_images, 'invalid_images': invalid_images,
              'attachment_mismatches': attachment_mismatches, 'matched_phase_group_starts': matched_starts,
              'phase_post_exposures': [{'phase': phase, 'post_id': post,
                                        'agents_by_arm': {a: len(ids) for a, ids in sorted(counts.items())}}
                                       for (phase, post), counts in sorted(exposures.items())],
              'future_post_observations': future_posts, 'future_quote_observations': future_quotes,
              'unobserved_phase_group_starts': absent_starts, 'mismatched_phase_group_starts': mismatched_starts,
              'source_version_unchanged': checkpoint_version(run) == version,
              'notice': 'Zero-call request assembly on recorded views. Not model receipt, token billing, autonomy or causal evidence.'}
    if not report['source_version_unchanged']:
        raise ValueError('source changed during request inspection')
    destination.mkdir(parents=True, exist_ok=False)
    write_lines(destination / 'request_examples.jsonl', examples.values())
    write_json(destination / 'report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--image-index', required=True)
    parser.add_argument('--image-root', action='append', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    print(json.dumps(inspect_requests(args.run, args.image_index, args.image_root, args.out), ensure_ascii=False))


if __name__ == '__main__':
    main()
