import pytest

from flowmirror.platform.browsing import BrowseSession

PRIV = {"persona": "cautious", "cash": 100.0, "holdings": {"X": 1},
        "memory": ["secret-note"], "beliefs": {"X": 0.5}, "risk_class": "low"}


def _card(pid, arm="T", **kw):
    base = {"post_id": pid, "org": "org", "title": "t " + pid,
            "caption": "cap " + pid, "arm": arm}
    base.update(kw)
    return base


def test_view_is_isolated_from_mutations():
    s = BrowseSession("a1", [_card("p1")], private_state=PRIV)
    v1 = s.view()
    v1["private_state"]["cash"] = 999.0
    v1["private_state"]["memory"].append("hack")
    v1["feed"][0]["title"] = "hacked"
    v1["agent_id"] = "spoof"
    s.apply({"kind": "like", "post_id": "p1"})
    v2 = s.view()
    assert v2["agent_id"] == "a1"
    assert v2["private_state"]["cash"] == 100.0
    assert v2["private_state"]["memory"] == ["secret-note"]
    assert v2["feed"][0]["title"] == "t p1"
    assert v2["counts"]["following"] == 0


@pytest.mark.parametrize("arm", ["T", "TC", "TV"])
def test_arm_dependent_exposure(arm):
    c = _card("p1", arm=arm, image_sha="sha-1", ocr_text="OCR TEXT",
              image_caption_frozen="frozen cap")
    s = BrowseSession("a1", [c])
    prev = s.view()["feed"][0]
    if arm == "TV":
        assert prev["image_sha"] == "sha-1"
    else:
        assert "image_sha" not in prev
    s.apply({"kind": "open", "post_id": "p1"})
    d = s.view()["detail"]
    assert d["is_detail"] is True
    if arm == "TC":
        assert d["ocr_text"] == "OCR TEXT"
        assert d["image_caption_frozen"] == "frozen cap"
        assert "image_sha" not in d
    elif arm == "TV":
        assert d["image_sha"] == "sha-1"
        assert "ocr_text" not in d
        assert "image_caption_frozen" not in d
    else:
        assert "image_sha" not in d and "ocr_text" not in d


def test_follow_visibility_rules():
    comments = [{"handle": "h1", "text": "a"}, {"handle": "h2", "text": "b"},
                {"handle": "h3", "text": "c"}, {"handle": "h4", "text": "d"}]
    s = BrowseSession("a1", [_card("p1", comments_prev=comments)], max_steps=20)
    r = s.apply({"kind": "follow", "handle": "h1"})
    assert (r["status"], r["reason"]) == ("rejected", "handle_not_visible")
    r = s.apply({"kind": "open", "post_id": "p1"})
    assert r["status"] == "accepted"
    assert s.apply({"kind": "follow", "handle": "h1"})["status"] == "accepted"
    r = s.apply({"kind": "follow", "handle": "h4"})
    assert (r["status"], r["reason"]) == ("rejected", "handle_not_visible")
    # Self-follow via alias: raw agent id "me" stays invisible, alias surfaces
    # only through a comment on an opened post.
    self_comments = [{"handle": "self-alias", "text": "my own"}]
    s2 = BrowseSession("me", [_card("p1", comments_prev=self_comments)],
                       own_handle="self-alias", max_steps=5)
    r = s2.apply({"kind": "follow", "handle": "me"})
    assert (r["status"], r["reason"]) == ("rejected", "handle_not_visible")
    assert s2.apply({"kind": "open", "post_id": "p1"})["status"] == "accepted"
    r = s2.apply({"kind": "follow", "handle": "self-alias"})
    assert (r["status"], r["reason"]) == ("rejected", "self_follow")


def test_invalid_actions_consume_budget():
    s = BrowseSession("a1", [_card("p1")], max_steps=2)
    r1 = s.apply({"kind": "like", "post_id": "nope"})
    r2 = s.apply({"kind": "bogus"})
    assert r1["status"] == "rejected" and r2["status"] == "rejected"
    assert s.done is True
    assert s.view()["remaining_steps"] == 0
    n = len(s.events)
    r3 = s.apply({"kind": "like", "post_id": "p1"})
    assert r3["status"] == "rejected"
    assert r3["reason"] == "session_closed"
    assert len(s.events) == n
    assert s.view()["remaining_steps"] == 0
    assert BrowseSession("a1", []).done is True


@pytest.mark.parametrize("bad_pid", [["p1"], {"post_id": "p1"}])
def test_malformed_post_id_rejected(bad_pid):
    s = BrowseSession("a1", [_card("p1")])
    r = s.apply({"kind": "like", "post_id": bad_pid})
    assert r["status"] == "rejected"
    assert s.view()["counts"]["opened"] == 0


def test_scroll_exposes_second_post_after_open():
    s = BrowseSession("a1", [_card("p1"), _card("p2")], page_size=1, max_steps=6)
    assert s.apply({"kind": "open", "post_id": "p1"})["status"] == "accepted"
    scroll_evt = s.apply({"kind": "scroll"})
    assert scroll_evt["status"] == "accepted"
    imps = [e for e in s.events if e["kind"] == "impression"
            and e["post_id"] == "p2"]
    assert len(imps) == 1
    assert scroll_evt["seq"] < imps[0]["seq"]
    assert s.view()["detail"] is None
    assert s.view()["feed"][0]["post_id"] == "p2"


def test_comment_promotion_and_recommendations():
    comments = [{"handle": None, "text": "anon"},
                {"handle": "f1", "text": "1"}, {"handle": "f2", "text": "2"},
                {"handle": "f3", "text": "3"}]
    s = BrowseSession("me", [_card("p1", comments_prev=comments)],
                      following=("f1", "f2", "f3"),
                      recommendations=[
                          {"handle": "me", "followers": 999},
                          {"handle": "z", "followers": 0},
                          {"handle": "big", "followers": 50},
                          {"handle": "aaa", "followers": 5},
                          {"handle": "zzz", "followers": 20},
                          {"handle": "dup", "followers": 10},
                          {"handle": "dup", "followers": 10},
                          {"followers": 7}])
    s.apply({"kind": "open", "post_id": "p1"})
    handles = [c["handle"] for c in s.view()["detail"]["comments_prev"]]
    assert handles == ["f1", "f2", None]
    assert handles.count(None) == 1  # anonymous retained
    recs = s.view()["recommendations"]
    assert [r["handle"] for r in recs] == ["big", "zzz", "dup"]
    assert all(isinstance(r["followers"], int) and r["followers"] > 0
               for r in recs)


def test_public_events_and_thread_safety():
    s = BrowseSession("a1", [_card("p1")], private_state=PRIV, max_steps=10)
    s.apply({"kind": "like", "post_id": "p1"})
    s.apply({"kind": "save", "post_id": "p1"})
    s.apply({"kind": "comment", "post_id": "p1", "text": "hello"})
    pe = s.public_events()
    kinds = [e["kind"] for e in pe]
    assert "save" not in kinds and "impression" not in kinds
    assert "like" in kinds and "comment" in kinds
    flat = repr(pe)
    assert "secret-note" not in flat and "cautious" not in flat

    from concurrent.futures import ThreadPoolExecutor
    ids = [f"agent{i}" for i in range(6)]
    sentinels = {i: [f"mem-{i}"] for i in ids}

    def run(aid):
        sess = BrowseSession(aid, [_card("p1")],
                             private_state={"memory": sentinels[aid]})
        sess.apply({"kind": "comment", "post_id": "p1", "text": "hi " + aid})
        return sess.public_events()

    with ThreadPoolExecutor(max_workers=6) as ex:
        outputs = list(ex.map(run, ids))
    for aid, out in zip(ids, outputs):
        assert len(out) == 1
        assert out[0]["agent_id"] == aid
        assert out[0]["text"] == "hi " + aid
    assert {o[0]["agent_id"] for o in outputs} == set(ids)


def test_nested_state_mutation_after_construction_is_ignored():
    priv = {"persona": "cautious", "cash": 100.0, "holdings": {"X": 1},
            "memory": ["secret-note"], "beliefs": {"X": 0.5}, "risk_class": "low"}
    cards = [_card("p1", comments_prev=[{"handle": "h1", "text": "a"}])]
    s = BrowseSession("a1", cards, private_state=priv)
    # Mutate caller-side structures after construction.
    priv["cash"] = 999.0
    priv["memory"].append("hack")
    priv["beliefs"]["X"] = 0.99
    cards[0]["title"] = "hacked"
    cards[0]["comments_prev"].append({"handle": "h2", "text": "b"})
    s.apply({"kind": "open", "post_id": "p1"})
    v = s.view()
    assert v["private_state"]["cash"] == 100.0
    assert v["private_state"]["memory"] == ["secret-note"]
    assert v["private_state"]["beliefs"]["X"] == 0.5
    assert v["feed"][0]["title"] == "t p1"
    assert [c["handle"] for c in v["detail"]["comments_prev"]] == ["h1"]


def test_event_lists_are_defensive_copies():
    s = BrowseSession("a1", [_card("p1")], private_state=PRIV, max_steps=10)
    s.apply({"kind": "like", "post_id": "p1"})
    ev = s.events
    ev.append({"kind": "fake"})
    ev[0]["agent_id"] = "spoof"
    pe = s.public_events()
    pe.append({"kind": "fake"})
    pe[0]["text"] = "spoofed"
    ev2 = s.events
    pe2 = s.public_events()
    assert len(ev2) == 2  # like + impression
    assert all(e["kind"] != "fake" for e in ev2)
    assert all(e["agent_id"] != "spoof" for e in ev2)
    assert all(e.get("text") != "spoofed" for e in pe2)
    assert len(pe2) == 1


def test_all_followed_comments_promoted_no_missing():
    comments = [{"handle": "f1", "text": "1"}, {"handle": "f2", "text": "2"},
                {"handle": "f3", "text": "3"},
                {"handle": "x1", "text": "x"}, {"handle": None, "text": "anon"}]
    s = BrowseSession("me", [_card("p1", comments_prev=comments)],
                      following=("f1", "f2", "f3"), max_steps=10)
    s.apply({"kind": "open", "post_id": "p1"})
    handles = [c["handle"] for c in s.view()["detail"]["comments_prev"]]
    # All three followed handles fill the three slots (none dropped);
    # the anonymous comment is covered separately, not a fourth slot.
    assert handles.count("f1") == 1
    assert handles.count("f2") == 1
    assert handles.count("f3") == 1
    assert handles == ["f1", "f2", "f3"]
