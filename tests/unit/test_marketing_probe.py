import copy
import json

import pytest

from flowmirror.market.tape import instant
from flowmirror.platform import marketing_probe
from flowmirror.platform.browse_run import normalize_spec, replay_run, run_browsing
from flowmirror.platform.browse_store import read_checkpoint
from flowmirror.platform.marketing import MarketingDesk
from flowmirror.platform.matched_probe import match_population
from flowmirror.platform.warehouse import lines, write_json, write_lines


def paired_source():
    """Tiny dated stand-ins for two source orgs, not actual research material."""
    cards = []
    for number, org, at in (
        (1, 'Alpha source org', '2025-11-01T09:00:00+08:00'),
        (2, 'Beta source org', '2025-11-02T04:00:00Z'),
        (3, 'Alpha source org', '2025-11-02T12:00:01+08:00'),
    ):
        cards.append({'post_id': f'xhs_source_{number}', 'org': org, 'market': 'CN',
                      'title': f'SOURCE_TITLE_{number}', 'caption': f'SOURCE_BODY_{number}',
                      'published_at': at, 'channel': 'xhs', 'time_basis': 'original_source_timestamp',
                      'image_refs': [f'asset_{number}_cover', f'asset_{number}_second'],
                      'image_descriptions': [f'cover {number}', f'second {number}'],
                      'image_caption_frozen': f'cover {number}\nsecond {number}', 'ocr_text': '',
                      'comments_prev': [{'handle': None, 'text': 'SOURCE_COMMENT_MUST_NOT_REPOST'}],
                      'provenance': {'source_row': number}})
    agents = [{'id': f'reader_{i}', 'handle': f'@reader_{i}', 'arm': ('T', 'TC', 'TV')[i % 3],
               'market': 'CN', 'market_instruments': [],
               'private_state': {'memory': [], 'cash': 10000, 'holdings': []}}
              for i in range(6)]
    return normalize_spec(match_population({
        'name': 'paired-tiny-source', 'phases': 3, 'max_steps': 4, 'page_size': 10,
        'phase_times': [f'2025-11-0{i}T12:00:00+08:00' for i in (1, 2, 3)],
        'policy_mode': 'external', 'policy_name': 'json-browse-matched-v1',
        'feed_policy': {'kind': 'recent_rotating', 'limit': 10, 'lookback_days': 30, 'seed': 19},
        'agents': agents, 'cards': cards, 'market_data': [],
        'corpus_info': {'source': 'test_fixture', 'selected_posts': 3},
    }, seed=19))


def build(source=None, **kwargs):
    return marketing_probe.build_marketing_probe(
        paired_source() if source is None else source, publication_budget=kwargs.pop('publication_budget', 3),
        **kwargs)


def test_all_orgs_exact_source_cards_pairing_and_input_immutability():
    source = paired_source()
    original = copy.deepcopy(source)
    spec, report = build(source)
    assert source == original
    assert spec['cards'] == []
    assert spec['agents'] == source['agents']
    assert spec['population_design'] == source['population_design']
    assert spec['feed_policy'] == source['feed_policy']
    assert spec['market_data'] == source['market_data']
    assert 'trading' not in spec and all('account' not in a for a in spec['agents'])
    assert spec['research_visibility'] == 'private' and 'SIMULATED' in spec['publication_notice']
    assert report['eligible_orgs'] == report['selected_institutions'] == 2
    assert report['sourceposts'] == report['selected_sourceposts'] == report['creatives'] == 3
    assert report['maximum_investor_calls'] == report['maximum_policy_calls'] == 6 * 3 * 4
    assert report['maximum_institution_rule_decisions'] == 2 * 3
    assert report['maximum_simulated_publications'] == 5
    assert report['model_calls'] == 0
    assert report['population']['resampled'] is False
    by_id = {c['post_id']: c for c in source['cards']}
    originals = [c['card'] for o in spec['marketing'] for c in o['creatives']]
    assert {c['post_id'] for c in originals} == set(by_id)
    for card in originals:
        assert card == by_id[card['post_id']]
        assert card is not by_id[card['post_id']]
    originals[0]['image_refs'].reverse()
    assert source == original


def test_sorted_org_ids_and_limited_orgs_do_not_duplicate_organic_exposures():
    source = paired_source()
    full, _ = build(source)
    source['cards'].reverse()
    source['agents'].reverse()
    limited, report = build(source, institution_limit=1)
    assert limited['marketing'][0] == full['marketing'][0]
    assert [o['id'] for o in full['marketing']] == ['institution_0001', 'institution_0002']
    assert [o['org'] for o in full['marketing']] == ['Alpha source org', 'Beta source org']
    assert [c['post_id'] for c in limited['cards']] == ['xhs_source_2']
    assert limited['agents'] == source['agents']
    assert report['institution_limit'] == 1 and report['institution_limit_applied']
    assert report['eligible_orgs'] == 2 and report['selected_institutions'] == 1
    assert report['organic_sourceposts'] == 1 and report['removed_from_organic'] == 2
    assert report['unselected_orgs_retained_organic'] == ['Beta source org']
    assert {c['post_id'] for c in limited['cards']}.isdisjoint(
        c['card']['post_id'] for o in limited['marketing'] for c in o['creatives'])


@pytest.mark.parametrize('strategy', ['rotate', 'feedback_select'])
def test_source_dates_restrict_institution_views_publication_and_comments(strategy):
    spec, report = build(strategy=strategy)
    assert [p['available_creatives'] for p in report['phase_availability']] == [1, 2, 3]
    assert [p['newly_available_creatives'] for p in report['phase_availability']] == [1, 1, 1]
    desk = MarketingDesk(spec['marketing'], spec['phases'], spec['phase_times'])
    for phase, at in enumerate(spec['phase_times']):
        for owner in spec['marketing']:
            view = desk.view(owner['id'], phase)
            assert all(instant(c['card']['published_at']) <= instant(at) for c in view['available_creatives'])
            if phase < 2:
                assert 'SOURCE_TITLE_3' not in json.dumps(view)
        cards = desk.begin_phase(phase)
        assert len({c['institution_id'] for c in cards}) == len(cards)
        for card in cards:
            assert card['published_at'] == at
            assert instant(card['source_published_at']) <= instant(at)
            assert card['comments_prev'] == []
            assert card['publication_kind'] == 'simulated_campaign'
            source = next(c for c in paired_source()['cards'] if c['post_id'] == card['source_post_id'])
            assert card['image_refs'] == source['image_refs']
            assert card['image_descriptions'] == source['image_descriptions']
        if phase == 0:
            assert desk.snapshots[-1]['decisions'][1]['action'] == {'kind': 'wait', 'reason': 'no_available_creative'}
        desk.finish_phase(phase)


@pytest.mark.parametrize('budget,schedule', [(0, None), (3, []), (1, [1, 2])])
def test_budget_and_schedule_allow_waits_with_one_publication_slot(budget, schedule):
    spec, report = build(publication_budget=budget, publish_phases=schedule)
    desk = MarketingDesk(spec['marketing'], spec['phases'], spec['phase_times'])
    total = 0
    for phase in range(spec['phases']):
        cards = desk.begin_phase(phase)
        total += len(cards)
        for owner in spec['marketing']:
            view = desk.view(owner['id'], phase)
            if any(c['institution_id'] == owner['id'] for c in cards):
                assert not view['publication_slot_available']
                action = {'kind': 'publish', 'creative_id': view['available_creatives'][0]['id']}
                assert desk.apply(view, action)[0]['status'] == 'rejected'
        desk.finish_phase(phase)
    assert total == (2 if budget == 1 else 0)
    assert total <= report['maximum_simulated_publications']
    assert report['maximum_institution_rule_decisions'] == 6


def test_population_scaling_copies_independent_triplets_not_sourceposts():
    source = paired_source()
    original = copy.deepcopy(source)
    spec, report = build(source, groups=5, max_steps=8, calls_per_hour=40)
    assert source == original
    assert report['agents'] == 15 and report['groups'] == 5
    assert report['population']['source_groups'] == 2
    assert report['population']['source_agents'] == 6
    assert report['population']['resampled'] is True
    assert report['sourceposts'] == report['selected_sourceposts'] == report['creatives'] == 3
    assert [c['source_feed_group'] for c in report['population']['copies']] == [0, 1, 0, 1, 0]
    assert report['population']['arm_counts'] == {'T': 5, 'TC': 5, 'TV': 5}
    assert report['maximum_investor_calls'] == 15 * 3 * 8
    assert report['maximum_institution_rule_decisions'] == 6
    assert report['elapsed_hours']['upper_bound_under_assumption'] == 9
    assert report['elapsed_hours']['is_forecast'] is False
    assert 'rate cap alone is insufficient' in report['elapsed_hours']['assumption']
    assert spec['feed_policy'] == source['feed_policy']
    assert len({a['id'] for a in spec['agents']}) == len({a['handle'] for a in spec['agents']}) == 15
    for group in range(5):
        actors = [a for a in spec['agents'] if a['feed_group'] == group]
        assert {a['arm'] for a in actors} == {'T', 'TC', 'TV'}
        assert actors[0]['private_state'] == actors[1]['private_state'] == actors[2]['private_state']
        prototype = next(a for a in source['agents'] if a['feed_group'] == group % 2)
        for actor in actors:
            for field in ('private_state', 'market', 'market_instruments'):
                assert actor[field] == prototype[field]
    spec['agents'][0]['private_state']['memory'].append('ONLY_FIRST_COPY')
    assert all('ONLY_FIRST_COPY' not in a['private_state']['memory'] for a in spec['agents'][1:])
    assert source == original


@pytest.mark.parametrize('groups', [None, 5])
def test_existing_pair_covariate_mismatch_is_rejected_before_scaling(groups):
    source = paired_source()
    source['agents'][0]['private_state']['cash'] += 1
    with pytest.raises(ValueError, match='source paired covariates differ: private_state'):
        build(source, groups=groups)


def test_unusable_sources_are_reported_without_invented_dates_orgs_or_galleries():
    source = paired_source()
    extra = []
    for index in range(5):
        extra.append({**copy.deepcopy(source['cards'][0]), 'post_id': f'excluded_{index}'})
    extra[0].pop('published_at')
    extra[1]['org'] = ''
    extra[2]['published_at'] = '2026-01-01T00:00:00+08:00'
    extra[3].pop('image_descriptions')
    extra[4]['synthetic'] = True
    source['cards'].extend(extra)
    spec, report = build(source)
    assert report['sourceposts'] == 8 and report['selected_sourceposts'] == 3
    assert report['excluded_sourceposts'] == {
        'missing_source_date': 1, 'missing_source_org': 1, 'source_date_after_last_phase': 1,
        'missing_or_unpaired_gallery': 1, 'not_original_source_material': 1,
    }
    assert spec['cards'] == []
    assert all(not c['card']['post_id'].startswith('excluded_') for o in spec['marketing'] for c in o['creatives'])


@pytest.mark.parametrize('kwargs,match', [
    ({'publication_budget': -1}, 'publication_budget'),
    ({'publication_budget': True}, 'publication_budget'),
    ({'groups': 0}, 'groups'), ({'institution_limit': 0}, 'institution_limit'),
    ({'max_steps': 1}, 'max_steps'), ({'publish_phases': [1, 1]}, 'publish_phases'),
    ({'publish_phases': [3]}, 'publish_phases'), ({'publish_phases': [True]}, 'publish_phases'),
    ({'calls_per_hour': 0}, 'calls_per_hour'), ({'calls_per_hour': float('inf')}, 'calls_per_hour'),
    ({'strategy': 'model'}, 'strategy'),
])
def test_invalid_options_are_rejected(kwargs, match):
    with pytest.raises(ValueError, match=match):
        build(**kwargs)


def test_accidental_accounts_and_unpaired_population_are_rejected():
    source = paired_source()
    source['agents'][0]['account'] = {'cash': 10000}
    with pytest.raises(ValueError, match='existing source spec.trading'):
        build(source)
    source = paired_source()
    source['agents'][0]['arm'] = source['agents'][1]['arm']
    with pytest.raises(ValueError, match='triplet'):
        build(source)


def test_existing_trading_is_preserved_without_new_fees_or_instruments():
    from flowmirror.platform.broker_demo import account_demo_spec

    source = account_demo_spec()
    # This fixture contributes an already complete simulation broker, never an
    # actual account or new mapping from source marketing cards to products.
    prototype = copy.deepcopy(next(a for a in source['agents'] if a['market'] == 'CN'))
    source['agents'] = [copy.deepcopy(prototype) for _ in range(3)]
    for i, actor in enumerate(source['agents']):
        actor.update(id=f'reader_{i}', handle=f'@reader_{i}')
    source = match_population(source)
    source['cards'] = copy.deepcopy(paired_source()['cards'])
    spec, report = build(source)
    assert spec['trading'] == source['trading']
    assert spec['market_data'] == source['market_data']
    assert spec['agents'] == source['agents']
    assert report['trading'] == 'existing_source_spec_trading_preserved'


def test_cli_uses_source_spec_copies_only_explicit_original_index_and_never_overwrites(tmp_path, monkeypatch):
    source = paired_source()
    source_path, index_path = tmp_path / 'source.json', tmp_path / 'original_index.jsonl'
    write_json(source_path, source)
    rows = [{'asset_ref': ref, 'path': f'D:/unscanned-private-images/{ref}.png', 'original_order': i}
            for i, ref in enumerate(r for c in source['cards'] for r in c['image_refs'])]
    rows.reverse()
    write_lines(index_path, [{'asset_ref': 'not_selected', 'path': 'not-a-real-file.png'}, *rows])
    source_bytes, index_bytes = source_path.read_bytes(), index_path.read_bytes()
    monkeypatch.setattr(marketing_probe, 'build_probe', lambda *a, **k: pytest.fail('redundant corpus import'))
    out = tmp_path / 'new-spec'
    args = ['--source-spec', str(source_path), '--out', str(out), '--publication-budget', '2',
            '--strategy', 'feedback_select', '--image-index', str(index_path), '--calls-per-hour', '24']
    assert marketing_probe.main(args) == 0
    assert sorted(p.name for p in out.iterdir()) == ['external_spec.json', 'image_index.jsonl', 'shadow_spec.json', 'support.json']
    assert list(lines(out / 'image_index.jsonl')) == rows
    external = json.loads((out / 'external_spec.json').read_text(encoding='utf-8'))
    shadow = json.loads((out / 'shadow_spec.json').read_text(encoding='utf-8'))
    support = json.loads((out / 'support.json').read_text(encoding='utf-8'))
    assert normalize_spec(external) == external
    assert normalize_spec(shadow) == shadow
    assert (shadow['policy_mode'], shadow['policy_name']) == ('scripted', 'warehouse-open-finish-v1')
    assert external['marketing'] == shadow['marketing']
    assert external['agents'] == shadow['agents']
    assert support['image_index']['copied_rows'] == 6
    assert support['elapsed_hours']['upper_bound_under_assumption'] == 3
    output_bytes = {p.name: p.read_bytes() for p in out.iterdir()}
    with pytest.raises(SystemExit) as exc:
        marketing_probe.main(args)
    assert exc.value.code == 2
    assert {p.name: p.read_bytes() for p in out.iterdir()} == output_bytes
    assert source_path.read_bytes() == source_bytes and index_path.read_bytes() == index_bytes
    no_index = tmp_path / 'no-index'
    assert marketing_probe.main(['--source-spec', str(source_path), '--out', str(no_index),
                                 '--publication-budget', '0', '--publish-phases']) == 0
    assert not (no_index / 'image_index.jsonl').exists()
    assert json.loads((no_index / 'support.json').read_text(encoding='utf-8'))['image_index']['explicitly_supplied'] is False


def test_cli_missing_explicit_gallery_index_does_not_write_partial_specs(tmp_path):
    source, index, out = tmp_path / 'source.json', tmp_path / 'index.jsonl', tmp_path / 'new'
    write_json(source, paired_source())
    write_lines(index, [])
    with pytest.raises(SystemExit) as exc:
        marketing_probe.main(['--source-spec', str(source), '--out', str(out),
                              '--publication-budget', '2', '--image-index', str(index)])
    assert exc.value.code == 2
    assert not out.exists()


def test_cli_optional_corpus_path_reuses_matched_builder(tmp_path, monkeypatch):
    calls = []

    def existing_builder(corpus, pool, images_root, **kwargs):
        calls.append((corpus, pool, images_root, kwargs))
        return paired_source(), [{'asset_ref': 'DO_NOT_SYNTHESIZE_INDEX'}], {'selected_posts': 3}

    monkeypatch.setattr(marketing_probe, 'build_probe', existing_builder)
    out = tmp_path / 'new'
    assert marketing_probe.main(['--corpus', 'corpus', '--pool', 'pool.jsonl', '--images-root', 'images',
                                 '--start', '2025-11-01', '--days', '3', '--publication-budget', '3',
                                 '--out', str(out)]) == 0
    assert calls[0][3] == {'start': '2025-11-01', 'days': 3, 'groups': 3, 'max_steps': 6, 'seed': 2027}
    assert not (out / 'image_index.jsonl').exists()


def test_offline_views_have_no_future_material_and_keep_every_paired_gallery(tmp_path):
    source = paired_source()
    spec, report = build(source)
    spec.update(policy_mode='scripted', policy_name='warehouse-open-finish-v1')
    seen_galleries, feeds = set(), {}
    source_by_id = {c['post_id']: c for c in source['cards']}
    expected_source = {0: 'xhs_source_1', 1: 'xhs_source_2', 2: 'xhs_source_3'}

    def inspect_and_open(view):
        if view['step'] == 0:
            selected = next(c for c in view['feed'] if c['source_post_id'] == expected_source[view['phase']])
            return {'kind': 'open', 'post_id': selected['post_id']}
        return {'kind': 'finish'}

    result = run_browsing(spec, tmp_path / 'fixture-run', inspect_and_open, max_new_calls=report['maximum_investor_calls'])
    assert result['status'] == 'completed' and result['model_calls'] == 0
    assert result['institution_rule_decisions'] == 6
    state = read_checkpoint(tmp_path / 'fixture-run')
    actors = {a['id']: a for a in spec['agents']}
    for frame in state['frames']:
        view, actor = frame['before'], actors[frame['agent_id']]
        phase, arm = frame['phase'], actor['arm']
        text = json.dumps(view)
        assert 'SOURCE_COMMENT_MUST_NOT_REPOST' not in text
        if phase < 2:
            assert 'SOURCE_TITLE_3' not in text and 'asset_3_' not in text and 'cover 3' not in text
        if phase < 1:
            assert 'SOURCE_TITLE_2' not in text and 'asset_2_' not in text and 'cover 2' not in text
        if frame['step'] == 0:
            feeds.setdefault((phase, actor['feed_group']), []).append([c['post_id'] for c in view['feed']])
        for card in view['feed']:
            original = source_by_id[card['source_post_id']]
            assert instant(original['published_at']) <= instant(view['sim_time'])
            if arm == 'TC':
                assert card['image_caption_frozen'] == original['image_descriptions'][0]
                assert 'image_refs' not in card
            elif arm == 'TV':
                assert card['image_refs'] == original['image_refs'][:1]
                assert 'image_caption_frozen' not in card
            else:
                assert 'image_refs' not in card and 'image_caption_frozen' not in card
        detail = view['detail']
        if detail:
            original = source_by_id[detail['source_post_id']]
            seen_galleries.add((arm, original['post_id']))
            if arm == 'TC':
                assert detail['image_caption_frozen'] == '\n'.join(
                    f'图片{i}：{description}' for i, description in enumerate(original['image_descriptions'], 1))
                assert 'image_refs' not in detail
            elif arm == 'TV':
                assert detail['image_refs'] == original['image_refs']
                assert 'image_caption_frozen' not in detail
            else:
                assert 'image_refs' not in detail and 'image_caption_frozen' not in detail
    assert seen_galleries == {(arm, post) for arm in ('T', 'TC', 'TV') for post in source_by_id}
    assert all(len(group) == 3 and group[0] == group[1] == group[2] for group in feeds.values())
    replay = replay_run(tmp_path / 'fixture-run')
    assert replay['identical'] and replay['complete']


def test_shadow_is_accepted_by_existing_offline_cli(tmp_path, capsys):
    from flowmirror.platform.browse_cli import main as browse_main

    source, out = tmp_path / 'source.json', tmp_path / 'new-spec'
    write_json(source, paired_source())
    marketing_probe.main(['--source-spec', str(source), '--out', str(out), '--publication-budget', '3'])
    assert browse_main(['--spec', str(out / 'shadow_spec.json'), '--out', str(tmp_path / 'offline'),
                        '--max-new-calls', '100']) == 0
    state = read_checkpoint(tmp_path / 'offline')
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert state['status'] == result['status'] == 'completed'
    assert result['model_calls'] == 0
    assert replay_run(tmp_path / 'offline')['identical']
