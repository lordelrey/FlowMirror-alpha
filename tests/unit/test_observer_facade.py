import pytest

from types import SimpleNamespace

from flowmirror.analysis.observer import RunObserver
from flowmirror.analysis import observer as observer_mod


def make_store(cfg, openings=None, agents=None, rows=None, days=None):
    agents = agents or {'A': {}, 'B': {}}
    days = days or {0: '2024-01-01', 1: '2024-01-02'}
    rows = rows or []
    meta = {'arms': {i: 'control' for i in agents}}
    if openings is not None:
        meta['openings'] = openings
    return SimpleNamespace(
        meta=meta,
        cfg=cfg,
        by_agent_day={},
        days=days,
        warnings=[],
        agents=agents,
        rows=rows,
        nav={'fund': {'2024-01-01': 10.0, '2024-01-02': 11.0}},
        notes={},
        posts=[],
        climate={},
    )


def make_observer(store):
    ob = RunObserver.__new__(RunObserver)
    ob.tag = 't'
    ob.s = store
    return ob


@pytest.fixture(autouse=True)
def no_cards(monkeypatch):
    monkeypatch.setattr(observer_mod, 'cards_for', lambda *a, **k: [])


# --- provider classification -------------------------------------------


def test_mock_llm_overrides_named_provider():
    ob = make_observer(make_store({'llm': {'model': 'gpt-4'}, 'mock_llm': True}))
    s = ob.summary()
    assert s['provider_mode'] == 'mock'
    assert s['model'] == 'mock'


def test_null_policy_overrides_both():
    ob = make_observer(make_store(
        {'llm': {'model': 'gpt-4'}, 'mock_llm': True, 'agent_policy': 'null'}))
    s = ob.summary()
    assert s['provider_mode'] == 'mock'
    assert s['model'] == 'null_policy'


def test_legacy_mock_flag():
    ob = make_observer(make_store({'llm': {'model': 'gpt-4'}, 'mock': True}))
    s = ob.summary()
    assert s['provider_mode'] == 'mock'
    assert s['model'] == 'mock'


def test_real_config_remains_real_model():
    ob = make_observer(make_store({'llm': {'model': 'gpt-4'}}))
    s = ob.summary()
    assert s['provider_mode'] == 'real_model'
    assert s['model'] == 'gpt-4'


def test_no_model_is_unknown():
    ob = make_observer(make_store({'llm': {}}))
    s = ob.summary()
    assert s['provider_mode'] == 'unknown'
    assert s['model'] is None


# --- openings semantics --------------------------------------------------


def subscribe_rows(fund='fund'):
    return [
        {'ev': 'act', 'kind': 'subscribe', 'i': 'A', 't': 0, 'fund': fund,
         'amt': 100.0, 'fee': 0.0, 'nav': 10.0, 'units': 10.0},
    ]


def test_known_no_opening_agent_gets_reconstruction():
    store = make_store(
        {'llm': {}},
        openings={'B': {}},
        rows=subscribe_rows(),
    )
    ob = make_observer(store)
    rows_before = [dict(r) for r in store.rows]
    frame = ob.agent_frame('A', 1)
    acct = frame['account']
    assert acct is not None
    assert acct['cash'] is None
    assert acct['return_pct'] is None
    assert acct['cash_change'] == pytest.approx(-100.0)
    pos = acct['positions'][0]
    assert pos['fund'] == 'fund'
    assert pos['units'] == pytest.approx(10.0)
    assert pos['cost_nav'] == pytest.approx(10.0)
    assert pos['market_value'] == pytest.approx(110.0)
    assert pos['unrealized_pnl'] == pytest.approx(10.0)
    assert 'total_return' not in acct
    assert 'holdings' not in acct
    assert 'cost' not in pos
    assert store.rows == rows_before  # source not mutated


def test_absent_openings_field_unknown():
    ob = make_observer(make_store({'llm': {}}, openings=None))
    assert ob.agent_frame('A', 1)['account'] is None


def test_openings_not_dict_unknown():
    ob = make_observer(make_store({'llm': {}}, openings=['x']))
    assert ob.agent_frame('A', 1)['account'] is None


def test_explicit_null_entry_unknown():
    ob = make_observer(make_store({'llm': {}}, openings={'A': None, 'B': {}}))
    assert ob.agent_frame('A', 1)['account'] is None
    assert ob.agent_frame('B', 1)['account'] is not None


@pytest.mark.parametrize('bad', ['bad', [], 0])
def test_explicit_bad_entry_unknown(bad):
    ob = make_observer(make_store({'llm': {}}, openings={'A': bad, 'B': {}}))
    assert ob.agent_frame('A', 1)['account'] is None
    assert ob.agent_frame('B', 1)['account'] is not None


def test_no_leak_of_other_agent_or_future_day():
    store = make_store(
        {'llm': {}},
        openings={},
        rows=[
            {'ev': 'act', 'kind': 'subscribe', 'i': 'A', 't': 0, 'fund': 'fund',
             'amt': 100.0, 'fee': 0.0, 'nav': 10.0, 'units': 10.0},
            {'ev': 'act', 'kind': 'subscribe', 'i': 'B', 't': 0, 'fund': 'fund',
             'amt': 999.0, 'fee': 0.0, 'nav': 10.0, 'units': 99.9},
            {'ev': 'act', 'kind': 'subscribe', 'i': 'A', 't': 1, 'fund': 'fund',
             'amt': 500.0, 'fee': 0.0, 'nav': 11.0, 'units': 45.45},
        ],
    )
    ob = make_observer(store)
    acct = ob.agent_frame('A', 0)['account']
    pos = acct['positions'][0]
    assert pos['fund'] == 'fund'
    assert pos['units'] == pytest.approx(10.0)
    assert pos['cost_nav'] == pytest.approx(10.0)
    assert pos['market_value'] == pytest.approx(100.0)
    assert len(acct['positions']) == 1
