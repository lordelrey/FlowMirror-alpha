"""Independent offline integration probes; all generated artifacts use tmp_path."""
import copy
import json

import pytest

from flowmirror.analysis.browse_index import build_index
from flowmirror.analysis.browse_observer import BrowseObserver
from flowmirror.analysis.marketing_behavior import build_report
from flowmirror.platform import browse_run, marketing
from flowmirror.platform.browse_driver import JsonBrowsePolicy
from flowmirror.platform.browse_journal import EVENTS, JournalFrames
from flowmirror.platform.browse_run import replay_run, run_browsing
from flowmirror.platform.browse_store import read_checkpoint


ARMS = ("T", "TC", "TV")
TIMES = [f"2026-01-{day:02d}T10:00:00+08:00" for day in range(1, 6)]
PNG = ("data:image/png;base64,"
       "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9ioAAAAASUVORK5CYII=")


def _creative(identity, name="A", **extra):
    return {"id": name, "card": {
        "post_id": f"source_{identity}_{name}", "org": f"Synthetic {identity}",
        "title": f"Synthetic {identity} {name}", "caption": "Synthetic caption only.",
        "market": "CN", "channel": "synthetic_fixture", "published_at": TIMES[0],
        "comments_prev": [],
        "image_refs": [f"asset_{identity}_{name}_{i}" for i in range(3)],
        "image_descriptions": [f"DESCRIPTION_{identity}_{name}_{i}" for i in range(3)],
        "ocr_text": f"OCR_{identity}_{name}", **extra,
    }}


def _owner(identity="desk_a", names=("A",), **extra):
    return {"id": identity, "org": f"Synthetic {identity}", "market": "CN",
            "strategy": "feedback_select", "publication_budget": 5,
            "creatives": [_creative(identity, name) for name in names], **extra}


def _spec(*, phases=3, groups=1, owners=None):
    return {"name": "independent_marketing_edges", "policy_mode": "scripted",
            "phases": phases, "phase_times": TIMES[:phases], "cards": [],
            "page_size": 12, "max_steps": 4,
            "agents": [{"id": f"investor_{group}_{arm}",
                        "handle": f"@reader_{group}_{arm}", "arm": arm,
                        "market": "CN", "feed_group": group,
                        "private_state": {"cash": 1000 + group,
                                          "memory": [f"PRIVATE_{group}_{arm}_SENTINEL"]}}
                       for group in range(groups) for arm in ARMS],
            "marketing": [_owner()] if owners is None else owners}


def _finish(_view):
    return {"kind": "finish"}


def _open_latest(view):
    if view["step"] == 0 and view["feed"]:
        return {"kind": "open", "post_id": view["feed"][-1]["post_id"]}
    return {"kind": "finish"}


def _engage_latest(view):
    if view["step"] == 0:
        return {"kind": "open", "post_id": view["feed"][-1]["post_id"]}
    if view["step"] in (1, 2):
        return {"kind": "like" if view["step"] == 1 else "save",
                "post_id": view["detail"]["post_id"]}
    return {"kind": "finish"}


def _choose_b_and_comment(view):
    if view["step"] == 0:
        chosen = next((p for p in view["feed"] if p.get("creative_id") == "B"), None)
        if chosen:
            return {"kind": "open", "post_id": chosen["post_id"]}
    if view["step"] == 1:
        return {"kind": "comment", "post_id": view["detail"]["post_id"],
                "text": f"Synthetic phase {view['phase']} public comment"}
    return {"kind": "finish"}


def _feedback(exposures, opened, *, like=0, save=0, comment=0):
    return {"exposures": exposures, "opened": opened, "opened_exposed": opened,
            "like": like, "save": save, "comment": comment,
            "open_rate": opened / exposures if exposures else None}


def _assert_completed(result):
    assert result["status"] == "completed", result
    assert result["model_calls"] == 0


@pytest.mark.parametrize("storage", ["snapshot", "journal"])
@pytest.mark.parametrize("release,first_phase", [
    ("2026-01-02T02:00:00Z", 1), ("2026-01-02T02:00:01Z", 2),
])
def test_dated_creatives_wait_for_clock_and_reposts_keep_provenance_only(
        tmp_path, storage, release, first_phase):
    owner = _owner(names=("A", "FUTURE"), strategy="rotate")
    original = owner["creatives"][0]["card"]
    original["comments_prev"] = [{"handle": "@reader_0_T", "text": "ORIGINAL_THREAD_ONLY"}]
    future = owner["creatives"][1]["card"]
    future.update(published_at=release, title="FUTURE_CREATIVE_SENTINEL",
                  comments_prev=[{"handle": "@obsolete_source_handle", "text": "OLD_FUTURE_COMMENT"}])
    spec = _spec(phases=4, owners=[owner])
    spec["cards"] = [copy.deepcopy(original)]
    before_spec = copy.deepcopy(spec)
    out = tmp_path / "dated"
    _assert_completed(run_browsing(spec, out, _open_latest, max_new_calls=100, storage=storage))
    state = read_checkpoint(out)
    decisions = [s["decisions"][0] for s in state["institution_snapshots"]]
    for phase, decision in enumerate(decisions):
        encoded = json.dumps(decision["before"])
        if phase < first_phase:
            assert "FUTURE_CREATIVE_SENTINEL" not in encoded
            assert future["post_id"] not in encoded
            assert decision["action"].get("creative_id") != "FUTURE"
        assert "ORIGINAL_THREAD_ONLY" not in encoded and "OLD_FUTURE_COMMENT" not in encoded
    assert decisions[first_phase]["action"]["creative_id"] == "FUTURE"
    publication_phases = {
        decision["result"]["post_id"]: phase
        for phase, decision in enumerate(decisions)
        if decision["action"]["kind"] == "publish"
        and decision["result"]["status"] == "accepted"
    }
    delivered_future = []
    for frame in state["frames"]:
        view = frame["before"]
        if frame["phase"] < first_phase:
            assert "FUTURE_CREATIVE_SENTINEL" not in json.dumps(view)
        for card in view["feed"] + ([view["detail"]] if view["detail"] else []):
            if card.get("publication_kind") != "simulated_campaign":
                continue
            source = future if card["creative_id"] == "FUTURE" else original
            assert card["source_post_id"] == source["post_id"]
            assert card["post_id"] != source["post_id"]
            assert card["source_published_at"] == source["published_at"]
            assert card.get("comments_prev", []) == []
            # Each repost has its own publication clock, including repeat creatives.
            publication_phase = publication_phases[card["post_id"]]
            assert card["published_at"] == TIMES[publication_phase]
            assert publication_phase <= frame["phase"]
            if card["creative_id"] == "FUTURE":
                delivered_future.append(frame["phase"])
                assert publication_phase >= first_phase
    assert delivered_future and min(delivered_future) == first_phase
    for phase, snapshot in enumerate(state["snapshots"]):
        assert snapshot["comments"][original["post_id"]][0]["text"] == "ORIGINAL_THREAD_ONLY"
        for post, comments in snapshot["comments"].items():
            if post != original["post_id"]:
                assert comments == []
        if phase < first_phase:
            future_post = decisions[first_phase]["result"]["post_id"]
            assert future_post not in snapshot["comments"]
    assert spec == before_spec
    assert replay_run(out)["identical"]


@pytest.mark.parametrize("seed", [0, 7])
@pytest.mark.parametrize("cover_alias", [False, True])
def test_paired_triplets_deliver_exact_cover_gallery_and_no_tc_pixels(tmp_path, seed, cover_alias):
    spec = _spec(phases=2, groups=2, owners=[_owner(), _owner("desk_b")])
    spec["cards"] = [_creative("organic")["card"]]
    sources = {c["post_id"]: c for c in spec["cards"]}
    sources.update({item["card"]["post_id"]: item["card"]
                    for owner in spec["marketing"] for item in owner["creatives"]})
    if cover_alias:
        for card in sources.values():
            card["image_sha"] = card["image_refs"][0]
    spec["feed_policy"] = {"kind": "recent_rotating", "limit": 2, "lookback_days": 10, "seed": seed}
    out = tmp_path / "paired"
    _assert_completed(run_browsing(spec, out, _open_latest, max_new_calls=100, storage="journal"))
    frames = list(read_checkpoint(out)["frames"])
    assert len(frames) == 24
    loaded = []

    def no_provider(*_args, **_kwargs):
        pytest.fail("request preparation invoked a provider")

    def loader(ref):
        loaded.append(ref)
        return PNG

    adapter = JsonBrowsePolicy(no_provider, model="offline-test-only", image_loader=loader)
    previews = {}
    saw_campaign = False
    for frame in frames:
        view = frame["before"]
        arm = next(a["arm"] for a in spec["agents"] if a["id"] == frame["agent_id"])
        group = next(a["feed_group"] for a in spec["agents"] if a["id"] == frame["agent_id"])
        if frame["step"] == 0:
            previews[(frame["phase"], group, arm)] = [
                (c["post_id"], c.get("source_post_id", c["post_id"]), c["title"], c["caption"])
                for c in view["feed"]]
        expected_refs, expected_sources = [], {}
        for surface, cards in (("feed", view["feed"]),
                               ("detail", [view["detail"]] if view["detail"] else [])):
            for card in cards:
                saw_campaign |= card.get("publication_kind") == "simulated_campaign"
                source = sources[card.get("source_post_id", card["post_id"])]
                refs = source["image_refs"][:1] if surface == "feed" else source["image_refs"]
                descriptions = source["image_descriptions"]
                assert card["arm"] == arm
                if arm == "TV":
                    assert card["image_refs"] == refs
                    assert "ocr_text" not in card and "image_caption_frozen" not in card
                    if cover_alias:
                        assert card["image_sha"] == source["image_refs"][0]
                    for ordinal, ref in enumerate(refs):
                        if ref not in expected_refs:
                            expected_refs.append(ref)
                        expected_sources.setdefault(ref, []).append({
                            "post_id": card["post_id"], "surface": surface, "image_index": ordinal})
                else:
                    assert "image_refs" not in card and "image_sha" not in card
                    if arm == "T":
                        assert "image_caption_frozen" not in card and "ocr_text" not in card
                    elif surface == "feed":
                        assert card["image_caption_frozen"] == descriptions[0]
                        assert "ocr_text" not in card
                    else:
                        assert card["image_caption_frozen"] == "\n".join(
                            f"图片{i + 1}：{value}" for i, value in enumerate(descriptions))
                        assert card["ocr_text"] == source["ocr_text"]
        loaded.clear()
        messages, delivery = adapter.prepare(view)
        parts = messages[1]["content"]
        assert loaded == expected_refs
        assert [part["type"] for part in parts] == ["text"] + ["image_url"] * len(expected_refs)
        if arm == "TV":
            assert delivery["attached_refs"] == expected_refs
            assert delivery["missing_refs"] == delivery["missing_shas"] == delivery["missing_post_ids"] == []
            assert delivery["visual_delivery"] == "image_parts_attached"
            assert delivery["image_attachments"] == [
                {"ref": ref, "part_index": i + 1, "sources": expected_sources[ref]}
                for i, ref in enumerate(expected_refs)]
        else:
            assert delivery["visual_delivery"] == "text_only"
            assert not delivery.get("image_attachments") and "data:image/" not in json.dumps(messages)
    assert saw_campaign and adapter.model_calls == 0
    for phase in range(2):
        for group in range(2):
            assert previews[(phase, group, "T")] == previews[(phase, group, "TC")] == previews[(phase, group, "TV")]
    assert replay_run(out)["identical"]


@pytest.mark.parametrize("storage", ["snapshot", "journal"])
@pytest.mark.parametrize("crash_index", [0, 11])
def test_persisted_response_before_apply_recovers_exact_marketing_feedback(
        tmp_path, monkeypatch, storage, crash_index):
    spec = _spec()
    out = tmp_path / "crashed"
    calls = []
    adapter = JsonBrowsePolicy(lambda *_a, **_k: pytest.fail("provider called"),
                               model="offline-test-only", image_loader=lambda _ref: PNG)

    def policy(view):
        calls.append(copy.deepcopy(view))
        _, policy.last_delivery = adapter.prepare(view)
        return _engage_latest(view)

    actual_apply = browse_run._World.apply

    def crash(world, action, index):
        if index == crash_index:
            raise SystemExit("test crash: response saved, world.apply not entered")
        return actual_apply(world, action, index)

    with monkeypatch.context() as patch:
        patch.setattr(browse_run._World, "apply", crash)
        with pytest.raises(SystemExit, match="world.apply not entered"):
            run_browsing(spec, out, policy, max_new_calls=100, storage=storage)
    saved = read_checkpoint(out)
    assert saved["status"] == "responded"
    assert len(saved["frames"]) == crash_index
    assert saved["policy_calls"] == len(calls) == crash_index + 1
    pending = copy.deepcopy(saved["pending"])
    assert pending["before"] == calls[-1]
    assert pending["action"] == _engage_latest(calls[-1])
    assert saved["institution_snapshots"][0]["feedback_after"] is None
    assert replay_run(out)["identical"] and replay_run(out)["pending"]
    pending_report = build_report(saved)
    assert pending_report["pending_excluded"] and pending_report["committed_frames"] == crash_index
    recovered = run_browsing(spec, out, lambda _view: pytest.fail("persisted response recalled policy"),
                             max_new_calls=0)
    assert recovered["new_policy_calls"] == 0 and len(calls) == crash_index + 1
    resumed = read_checkpoint(out)
    assert resumed["pending"] is None and len(resumed["frames"]) == crash_index + 1
    recovered_frame = resumed["frames"][crash_index]
    assert recovered_frame["before"] == pending["before"]
    assert recovered_frame["action"] == pending["action"]
    assert recovered_frame["delivery"] == pending["delivery"]
    if crash_index == 11:
        assert resumed["institution_snapshots"][0]["feedback_after"] == {
            "desk_a": {"A": _feedback(3, 3, like=3, save=3)}}
        assert resumed["institution_snapshots"][1]["decisions"][0]["before"]["feedback"] == {
            "A": _feedback(3, 3, like=3, save=3)}
    _assert_completed(run_browsing(spec, out, policy, max_new_calls=100))
    assert len(calls) == 36 and adapter.model_calls == 0
    fresh_out = tmp_path / "fresh"
    _assert_completed(run_browsing(spec, fresh_out, policy, max_new_calls=100, storage=storage))
    final, fresh = read_checkpoint(out), read_checkpoint(fresh_out)
    assert list(final["frames"]) == list(fresh["frames"])
    assert final["snapshots"] == fresh["snapshots"]
    assert final["institution_snapshots"] == fresh["institution_snapshots"]
    assert build_report(final) == build_report(fresh)
    assert final["institution_snapshots"][-1]["feedback_after"] == {
        "desk_a": {"A": _feedback(18, 9, like=9, save=9)}}
    assert replay_run(out)["identical"] and replay_run(out)["complete"]


@pytest.mark.parametrize("interval", [1, 4])
def test_storage_resume_phase_lag_and_indexed_observer_match(tmp_path, monkeypatch, interval):
    spec = _spec(phases=4, owners=[_owner(names=("A", "B"))])
    spec["offline_checkpoint_interval"] = interval
    roots = {name: tmp_path / name for name in ("snapshot", "journal", "resumed")}
    for name in ("snapshot", "journal"):
        _assert_completed(run_browsing(spec, roots[name], _choose_b_and_comment,
                                      max_new_calls=100, storage=name))
    paused = run_browsing(spec, roots["resumed"], _choose_b_and_comment,
                          max_new_calls=5, storage="journal")
    assert paused["status"] == "paused" and replay_run(roots["resumed"])["identical"]
    _assert_completed(run_browsing(spec, roots["resumed"], _choose_b_and_comment, max_new_calls=100))
    states = {name: read_checkpoint(root) for name, root in roots.items()}
    reference = states["snapshot"]
    for state in states.values():
        assert list(state["frames"]) == list(reference["frames"])
        assert state["snapshots"] == reference["snapshots"]
        assert state["institution_snapshots"] == reference["institution_snapshots"]
        assert build_report(state) == build_report(reference)
    decisions = [s["decisions"][0] for s in reference["institution_snapshots"]]
    assert [d["action"].get("creative_id") for d in decisions] == ["A", "B", "B", "B"]
    assert decisions[1]["before"]["feedback"] == {"A": _feedback(3, 0)}
    assert decisions[2]["before"]["feedback"]["B"] == _feedback(3, 3, comment=3)
    assert decisions[3]["before"]["feedback"]["B"] == _feedback(9, 6, comment=6)
    for frame in reference["frames"]:
        detail = frame["before"]["detail"]
        if detail and frame["phase"] == 1:
            assert detail["comments_prev"] == []
        if detail and frame["phase"] == 2:
            assert len(detail["comments_prev"]) == 3
            assert all(c["text"] == "Synthetic phase 1 public comment" for c in detail["comments_prev"])
    for root in roots.values():
        assert replay_run(root)["identical"] and replay_run(root)["complete"]
    source_files = [roots["journal"] / "browse_run.json", roots["journal"] / EVENTS]
    before_bytes = {path: path.read_bytes() for path in source_files}
    index_path = tmp_path / "private_index.json"
    build_index(roots["journal"], index_path)
    normal = BrowseObserver(roots["journal"])
    expected_summary = normal.summary()
    expected_traces = {(a["id"], phase): normal.agent_trace(a["id"], phase)
                       for a in spec["agents"] for phase in range(spec["phases"])}
    monkeypatch.setattr(JournalFrames, "load", lambda *_a, **_k: pytest.fail("index fell back to full journal scan"))
    indexed = BrowseObserver(roots["journal"], index_path)
    assert indexed.indexed and indexed.summary() == expected_summary
    for (actor, phase), expected in expected_traces.items():
        trace = indexed.agent_trace(actor, phase)
        assert indexed.indexed and trace == expected
        assert trace["institution_before"] == {"phase": phase, "decisions": [decisions[phase]]}
        assert "feedback_after" not in trace["institution_before"]
        assert trace["institution_before"]["decisions"][0]["before"]["feedback_through_phase"] == phase - 1
        for other in spec["agents"]:
            if other["id"] != actor:
                assert other["private_state"]["memory"][0] not in json.dumps(trace)
    assert all(path.read_bytes() == content for path, content in before_bytes.items())


def test_institution_policy_inputs_and_investor_inputs_are_isolated(tmp_path, monkeypatch):
    owners = [_owner("desk_a", ("ONLY_A",)), _owner("desk_b", ("ONLY_B",))]
    for owner in owners:
        owner["creatives"].append(_creative(owner["id"], "FUTURE", title=f"HIDDEN_{owner['id']}",
                                             published_at=TIMES[4]))
        owner["creatives"][0]["card"]["private_state"] = {"memory": ["SOURCE_PRIVATE_SENTINEL"]}
    spec = _spec(phases=3, owners=owners)
    institution_views, investor_views = [], []
    original_choose = marketing.choose_marketing_action

    def choose(view):
        institution_views.append(copy.deepcopy(view))
        response = original_choose(view)
        view["available_creatives"][0]["card"]["title"] = "POLICY_MUTATED_COPY_SENTINEL"
        return response

    def policy(view):
        investor_views.append(copy.deepcopy(view))
        return _open_latest(view)

    monkeypatch.setattr(marketing, "choose_marketing_action", choose)
    out = tmp_path / "isolated"
    _assert_completed(run_browsing(spec, out, policy, max_new_calls=100, storage="journal"))
    assert len(institution_views) == 6 and len(investor_views) == 18
    for view in institution_views:
        other = "desk_b" if view["institution_id"] == "desk_a" else "desk_a"
        text = json.dumps(view)
        assert other not in text and "HIDDEN_" not in text
        assert "SOURCE_PRIVATE_SENTINEL" not in text and "POLICY_MUTATED_COPY_SENTINEL" not in text
        assert "private_state" not in text and "agent_id" not in text
        for actor in spec["agents"]:
            assert actor["id"] not in text and actor["private_state"]["memory"][0] not in text
        assert all(c["card"]["org"] == view["org"] for c in view["available_creatives"])
    for view in investor_views:
        text = json.dumps(view)
        assert "available_creatives" not in text and "remaining_publications" not in text
        assert "feedback_through_phase" not in text and "HIDDEN_" not in text
        assert "SOURCE_PRIVATE_SENTINEL" not in text and "POLICY_MUTATED_COPY_SENTINEL" not in text
        for actor in spec["agents"]:
            marker = actor["private_state"]["memory"][0]
            assert (marker in text) == (actor["id"] == view["agent_id"])
    saved = read_checkpoint(out)
    assert "POLICY_MUTATED_COPY_SENTINEL" not in json.dumps(saved["institution_snapshots"])
    assert replay_run(out)["identical"]


def test_cross_institution_publish_and_current_future_feedback_are_excluded():
    owners = [_owner("desk_a", ("ONLY_A",)), _owner("desk_b", ("ONLY_B",))]
    desk = marketing.MarketingDesk(owners, 3, TIMES[:3])
    own_view = desk.view("desk_a", 0)
    result, card = desk.apply(own_view, {"kind": "publish", "creative_id": "ONLY_B"})
    assert result["status"] == "rejected" and card is None and desk.published == []
    forged_view = copy.deepcopy(own_view)
    forged_view["available_creatives"] = desk.view("desk_b", 0)["available_creatives"]
    with pytest.raises(ValueError, match="observation changed"):
        desk.apply(forged_view, {"kind": "publish", "creative_id": "ONLY_B"})
    cards = {card["institution_id"]: card for card in desk.begin_phase(0)}

    def record(phase, post, actor):
        desk.ledger.record({"phase": phase, "agent_id": actor,
                            "before": {"feed": [{"post_id": post}], "detail": None},
                            "action": {"kind": "open", "post_id": post},
                            "result": {"status": "accepted"}}, "T")

    record(0, cards["desk_a"]["post_id"], "PAST_PRIVATE_READER")
    for index in range(4):
        record(0, cards["desk_b"]["post_id"], f"OTHER_INSTITUTION_PRIVATE_{index}")
    desk.finish_phase(0)
    expected = copy.deepcopy(desk.view("desk_a", 1))
    assert expected["feedback"] == {"ONLY_A": _feedback(1, 1)}
    record(1, cards["desk_a"]["post_id"], "CURRENT_PRIVATE_READER")
    record(2, cards["desk_a"]["post_id"], "FUTURE_PRIVATE_READER")
    assert desk.view("desk_a", 1) == expected
    assert desk.view("desk_b", 1)["feedback"] == {"ONLY_B": _feedback(4, 4)}
    assert desk.begin_phase(1)[0]["institution_id"] == "desk_a"
    assert desk.snapshots[1]["decisions"][0]["before"] == expected
    assert "PRIVATE" not in json.dumps(desk.snapshots)


@pytest.mark.parametrize("storage", ["snapshot", "journal"])
@pytest.mark.parametrize("openers,threshold,reason", [
    (0, 0, "zero_observed_openings"),
    (0, 0.6, "zero_observed_openings"),
    (1, 0.6, "observed_open_rate_below_threshold"),
])
def test_low_open_rate_waits_without_spending_publications_or_forcing_follow(
        tmp_path, storage, openers, threshold, reason):
    spec = _spec(owners=[_owner(min_open_rate=threshold)])

    def policy(view):
        if openers and view["agent_id"] == "investor_0_T":
            return _open_latest(view)
        return _finish(view)

    out = tmp_path / "low_openings"
    result = run_browsing(spec, out, policy, max_new_calls=100, storage=storage)
    _assert_completed(result)
    assert result["simulated_publications"] == 1 and result["institution_rule_decisions"] == 3
    state = read_checkpoint(out)
    for phase in (1, 2):
        decision = state["institution_snapshots"][phase]["decisions"][0]
        assert decision["action"] == {"kind": "wait", "reason": reason}
        assert decision["before"]["remaining_publications"] == 4
        assert decision["before"]["feedback"] == {"A": _feedback(phase * 3, phase * openers)}
    assert state["institution_snapshots"][-1]["feedback_after"] == {
        "desk_a": {"A": _feedback(9, 3 * openers)}}
    for snapshot in state["snapshots"]:
        assert snapshot["edges"] == [] and set(snapshot["followers"].values()) == {0}
    for frame in state["frames"]:
        assert frame["action"]["kind"] in ("open", "finish")
        assert frame["before"]["following"] == frame["after"]["following"] == []
        assert frame["before"]["recommendations"] == []
    report = build_report(state)
    assert report["marketing"]["publication_count"] == 1 and report["marketing"]["wait_count"] == 2
    assert replay_run(out)["identical"] and replay_run(out)["complete"]
