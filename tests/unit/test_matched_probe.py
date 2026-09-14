import copy
import json

import pytest
from PIL import Image

from flowmirror.platform.browse_assets import IndexedImages
from flowmirror.platform.browse_cli import demo_spec
from flowmirror.platform.browse_driver import JsonBrowsePolicy
from flowmirror.platform.browse_run import normalize_spec, run_browsing, replay_run
from flowmirror.platform.browse_store import read_checkpoint, checkpoint_version
from flowmirror.platform.matched_probe import build_probe, match_population
from flowmirror.platform.warehouse import write_lines, warehouse_policy
from flowmirror.analysis.browse_requests import inspect_requests


def source_fixture(tmp_path):
    corpus = tmp_path / 'corpus'; corpus.mkdir()
    images = tmp_path / 'images'; images.mkdir()
    for name in ('a.png', 'b.png', 'c.png'):
        Image.new('RGB', (2, 2), 'white').save(images / name)
    cards = [{'post_id': f'xhs_n{i}', 'market': 'CN', 'channel': 'xhs', 'org': 'institution',
              'title': f'Institution post {i}', 'caption': 'Same source post text',
              'published_at': f'2025-11-0{min(i, 3)}T10:00:00+08:00',
              'image_refs': ['asset_whole_note_other_image'], 'ocr_text': 'UNALIGNED OCR',
              'image_caption_frozen': 'UNALIGNED aggregate', 'comments_prev': []}
             for i in range(1, 4)]
    write_lines(corpus / 'posts.jsonl', cards)
    write_lines(corpus / 'nav.jsonl', [{'fund_code': '000001', 'nav_date': '2025-10-31', 'nav': 1.0}])
    pool = tmp_path / 'pool.jsonl'
    write_lines(pool, [{'note_id': 'n1', 'image_ids': ['a.png', 'b.png'],
                       'image_caption_frozen': ['cover description', 'second image description'],
                       'caption_meta': {'model': 'fixture', 'leak_flags': []}},
                      {'note_id': 'n2', 'image_ids': ['c.png'], 'image_caption_frozen': ['later description']}])
    return corpus, pool, images


def prepared_spec(tmp_path, groups=2):
    corpus, pool, images = source_fixture(tmp_path)
    spec, index, report = build_probe(corpus, pool, images, start='2025-11-01', days=2, groups=groups)
    image_index = tmp_path / 'image_index.jsonl'; write_lines(image_index, index)
    return spec, image_index, images, report


def offline(spec):
    spec = copy.deepcopy(spec)
    spec.update(policy_mode='scripted', policy_name='warehouse-open-finish-v1')
    return spec


def test_matched_population_is_balanced_and_does_not_alias_or_rewrite_input(tmp_path):
    spec, _, _, _ = prepared_spec(tmp_path)
    original = copy.deepcopy(spec)
    again = match_population(spec, seed=2027)
    assert spec == original
    assert again == spec
    assert match_population(spec, seed=2028) != spec
    for group in spec['population_design']['groups']:
        actors = [a for a in spec['agents'] if a['id'] in group['agents']]
        assert {a['arm'] for a in actors} == {'T', 'TC', 'TV'}
        for field in ('private_state', 'market', 'market_instruments'):
            assert all(a[field] == actors[0][field] for a in actors)
        actors[0]['private_state']['memory'].append('ONLY_FIRST_ACTOR')
        assert all('ONLY_FIRST_ACTOR' not in a['private_state']['memory'] for a in actors[1:])
    with pytest.raises(ValueError, match='multiple of three'):
        match_population({**spec, 'agents': spec['agents'][:4]})


def test_pool_pairing_uses_exact_gallery_without_unaligned_ocr(tmp_path):
    spec, index, images, report = prepared_spec(tmp_path)
    assert report['candidate_posts'] == report['selected_posts'] == 2
    assert report['selected_images'] == 3 and report['maximum_policy_calls'] == 72
    card = spec['cards'][0]
    assert card['image_refs'] == ['asset_pool_n1_0', 'asset_pool_n1_1']
    assert card['image_descriptions'] == ['cover description', 'second image description']
    assert card['ocr_text'] == ''
    assert 'UNALIGNED' not in json.dumps(spec)
    assert IndexedImages(index, [images])(card['image_refs'][0]).startswith('data:image/png;base64,')
    assert spec['policy_mode'] == 'external' and spec['offline_checkpoint_interval'] == 1


@pytest.mark.parametrize('broken,reason', [
    ({'image_ids': ['missing.png'], 'image_caption_frozen': ['text']}, 'missing_image_file'),
    ({'image_ids': ['../outside.png'], 'image_caption_frozen': ['text']}, 'image_path_outside_root_or_non_raster'),
    ({'image_ids': ['a.png'], 'image_caption_frozen': []}, 'missing_or_unpaired_description'),
    ({'image_ids': ['a.png', 'a.png'], 'image_caption_frozen': ['one', 'two']}, 'missing_or_unpaired_description'),
    ({'image_ids': [{}], 'image_caption_frozen': ['text']}, 'missing_or_unpaired_description'),
    ({'image_ids': ['a.png'], 'image_caption_frozen': ['']}, 'missing_or_unpaired_description'),
])
def test_incomplete_galleries_are_not_silently_partially_selected(tmp_path, broken, reason):
    corpus, _, images = source_fixture(tmp_path)
    pool = tmp_path / 'broken_pool.jsonl'
    write_lines(pool, [{'note_id': 'n1', **broken},
                      {'note_id': 'n2', 'image_ids': ['c.png'], 'image_caption_frozen': ['valid']}])
    spec, index, report = build_probe(corpus, pool, images, start='2025-11-01', days=3)
    assert len(spec['cards']) == 1 and spec['cards'][0]['post_id'] == 'xhs_n2'
    assert len(index) == 1
    assert report['excluded_posts'][reason] == 1
    assert report['excluded_posts']['no_source_pool_record'] == 1


def test_no_eligible_pool_does_not_fabricate_a_us_or_cn_description(tmp_path):
    corpus, _, images = source_fixture(tmp_path)
    pool = tmp_path / 'empty_pool.jsonl'; write_lines(pool, [])
    with pytest.raises(ValueError, match='no dated posts'):
        build_probe(corpus, pool, images, start='2025-11-01')


def test_rendered_preview_and_gallery_preserve_t_tc_tv_separation(tmp_path):
    spec, _, _, _ = prepared_spec(tmp_path, groups=1)
    root = tmp_path / 'run'
    run_browsing(offline(spec), root, warehouse_policy, max_new_calls=100, storage='journal')
    actors = {a['id']: a for a in spec['agents']}
    saw_tc_cover = saw_tc_gallery = saw_tv_gallery = False
    for frame in read_checkpoint(root)['frames']:
        arm = actors[frame['agent_id']]['arm']
        view = frame['before']
        assert all(p['post_id'] == 'xhs_n1' for p in view['feed']) if frame['phase'] == 0 else True
        for p in view['feed']:
            if arm == 'TC':
                assert p['image_caption_frozen'] in ('cover description', 'later description')
                assert 'image_refs' not in p
                saw_tc_cover = True
            elif arm == 'TV':
                assert len(p['image_refs']) == 1 and 'image_caption_frozen' not in p
            else:
                assert 'image_refs' not in p and 'image_caption_frozen' not in p
        detail = view['detail']
        if detail and detail['post_id'] == 'xhs_n1':
            if arm == 'TC':
                assert detail['image_caption_frozen'] == '图片1：cover description\n图片2：second image description'
                assert detail['ocr_text'] == '' and 'image_refs' not in detail
                saw_tc_gallery = True
            elif arm == 'TV':
                assert len(detail['image_refs']) == 2 and 'image_caption_frozen' not in detail
                saw_tv_gallery = True
    assert saw_tc_cover and saw_tc_gallery and saw_tv_gallery
    assert replay_run(root)['complete']


def test_explicit_feed_groups_survive_reordered_population(tmp_path):
    spec, _, _, _ = prepared_spec(tmp_path)
    # Extra dated posts make the group rotation observable.
    spec['cards'] = [{**spec['cards'][0], 'post_id': f'p{i}'} for i in range(30)]
    results = []
    for n, agents in enumerate((spec['agents'], list(reversed(spec['agents'])))):
        variant = offline({**spec, 'agents': agents})
        run_browsing(variant, tmp_path / str(n), warehouse_policy, max_new_calls=100)
        results.append({(f['agent_id'], f['phase']): [p['post_id'] for p in f['before']['feed']]
                        for f in read_checkpoint(tmp_path / str(n))['frames'] if f['step'] == 0})
    assert results[0] == results[1]
    assert results[0][(spec['agents'][0]['id'], 0)] != results[0][(spec['agents'][3]['id'], 0)]
    for invalid in (-1, True, '0'):
        variant = copy.deepcopy(spec); variant['agents'][0]['feed_group'] = invalid
        with pytest.raises(ValueError, match='feed_group'):
            normalize_spec(variant)


def test_malformed_aligned_card_is_rejected_but_legacy_cards_are_unchanged():
    spec = demo_spec()
    normalize_spec(spec)
    spec['cards'][0].update(image_descriptions=['one', 'two'], image_refs=['asset_one'])
    with pytest.raises(ValueError, match='image_descriptions'):
        normalize_spec(spec)


def test_prepare_is_side_effect_free_and_maps_repeated_pixels_to_both_posts(tmp_path):
    spec, index, images, _ = prepared_spec(tmp_path)
    loader = IndexedImages(index, [images])
    calls = []
    policy = JsonBrowsePolicy(lambda messages, **kw: calls.append(messages) or {'parsed': {'kind': 'finish'}}, image_loader=loader)
    view = {'private_state': {'memory': ['ONLY_OWN']}, 'feed': [
        {'post_id': 'p1', 'arm': 'TV', 'image_refs': ['asset_pool_n1_0']},
        {'post_id': 'p2', 'arm': 'TV', 'image_refs': ['asset_pool_n1_0']}],
        'detail': {'post_id': 'p1', 'arm': 'TV', 'image_refs': ['asset_pool_n1_0', 'asset_pool_n1_1']}}
    original = copy.deepcopy(view)
    messages, delivery = policy.prepare(view)
    assert view == original and policy.model_calls == 0 and calls == []
    assert not hasattr(policy, 'last_delivery')
    assert len(messages[1]['content']) == 3
    assert delivery['image_attachments'][0]['sources'] == [
        {'post_id': 'p1', 'surface': 'feed', 'image_index': 0},
        {'post_id': 'p2', 'surface': 'feed', 'image_index': 0},
        {'post_id': 'p1', 'surface': 'detail', 'image_index': 0}]
    assert delivery['image_attachments'][1]['sources'] == [{'post_id': 'p1', 'surface': 'detail', 'image_index': 1}]
    assert 'base64' not in json.dumps(delivery)
    assert policy(view) == {'kind': 'finish'}
    assert calls == [messages] and policy.model_calls == 1 and policy.last_delivery == delivery
    with pytest.raises(TypeError):
        policy({'non_json': object()})
    assert policy.last_delivery == {} and policy.model_calls == 1 and calls == [messages]
    for arm in ('T', 'TC'):
        own = copy.deepcopy(view)
        for card in own['feed'] + [own['detail']]:
            card['arm'] = arm
        prepared, metadata = policy.prepare(own)
        assert len(prepared[1]['content']) == 1 and metadata['visual_delivery'] == 'text_only'


def test_image_loader_refuses_unlisted_or_outside_paths_and_detects_missing_files(tmp_path):
    _, index, images, _ = prepared_spec(tmp_path)
    loader = IndexedImages(index, [images])
    assert loader('asset_unknown') is None
    loader.paths['asset_outside'] = tmp_path / 'outside.png'
    Image.new('RGB', (2, 2)).save(tmp_path / 'outside.png')
    assert loader('asset_outside') is None
    loader.paths['asset_missing'] = images / 'missing.png'
    assert loader('asset_missing') is None
    policy = JsonBrowsePolicy(None, image_loader=loader)
    _, metadata = policy.prepare({'feed': [{'post_id': 'p', 'arm': 'TV', 'image_refs': ['asset_missing']} ]})
    assert metadata['missing_refs'] == ['asset_missing'] and metadata['visual_delivery'] == 'tv_unavailable'


def test_request_inspection_uses_actual_frames_and_redacts_all_image_parts(tmp_path, monkeypatch):
    spec, index, images, _ = prepared_spec(tmp_path)
    root = tmp_path / 'run'
    run_browsing(offline(spec), root, warehouse_policy, max_new_calls=100, storage='journal')
    version = checkpoint_version(root)
    report = inspect_requests(root, index, [images], tmp_path / 'review')
    assert report['prepared_frames'] == 24 and report['model_calls'] == 0
    assert report['matched_phase_group_starts'] == 4 and report['unobserved_phase_group_starts'] == 0
    assert report['mismatched_phase_group_starts'] == report['missing_images'] == report['invalid_images'] == []
    assert report['future_post_observations'] == report['future_quote_observations'] == []
    early = [r for r in report['phase_post_exposures'] if r['phase'] == 0]
    late = [r for r in report['phase_post_exposures'] if r['phase'] == 1]
    assert len(early) == 1 and early[0]['post_id'] == 'xhs_n1'
    assert len(late) == 2 and all(r['agents_by_arm'] == {'T': 2, 'TC': 2, 'TV': 2} for r in late)
    assert report['by_arm']['TV']['image_parts'] > 0
    assert report['by_arm']['TC']['image_parts'] == report['by_arm']['T']['image_parts'] == 0
    saved = (tmp_path / 'review' / 'request_examples.jsonl').read_text(encoding='utf-8')
    assert 'data:image/' not in saved and 'base64,' not in saved and 'decoded_bytes' in saved
    assert checkpoint_version(root) == version
    with pytest.raises(FileExistsError):
        inspect_requests(root, index, [images], tmp_path / 'review')
    with pytest.raises(ValueError, match='outside'):
        inspect_requests(root, index, [images], root / 'reports')
    prepare = JsonBrowsePolicy.prepare
    def missing_mapping(self, view):
        messages, delivery = prepare(self, view)
        delivery.pop('image_attachments', None)
        return messages, delivery
    monkeypatch.setattr(JsonBrowsePolicy, 'prepare', missing_mapping)
    bad = inspect_requests(root, index, [images], tmp_path / 'bad_mapping')
    assert bad['attachment_mismatches']
    assert 'base64,' not in (tmp_path / 'bad_mapping' / 'request_examples.jsonl').read_text(encoding='utf-8')


def test_viewer_can_combine_asset_indexes_without_overwriting_other_sources(tmp_path):
    from web.community_server import _State
    _, index, images, _ = prepared_spec(tmp_path)
    extra = tmp_path / 'extra.jsonl'
    write_lines(extra, [{'asset_ref': 'asset_warehouse', 'path': str(images / 'c.png')}])
    state = _State(tmp_path, None, asset_index=[index, extra], asset_roots=[images])
    assert state.asset_path('asset_warehouse') == images / 'c.png'
    assert state.asset_path('asset_pool_n1_0') == images / 'a.png'
    conflict = tmp_path / 'conflict.jsonl'
    write_lines(conflict, [{'asset_ref': 'asset_pool_n1_0', 'path': str(images / 'b.png')}])
    with pytest.raises(ValueError, match='conflicting image reference'):
        _State(tmp_path, None, asset_index=[index, conflict], asset_roots=[images])
