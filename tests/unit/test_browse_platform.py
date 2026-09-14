import copy
import json

import pytest

from flowmirror.platform.public_board import PublicBoard
from flowmirror.platform.browsing import BrowseSession
from flowmirror.platform.browse_driver import JsonBrowsePolicy, drive_session


def make_cards():
    return [{
        "post_id": "p1",
        "title": "fixture",
        "caption": "fictional fixture",
        "comments_prev": [{"handle": "@b", "text": "B seed public comment"}],
    }]


def make_handles():
    return {"a": "@a", "b": "@b", "c": "@c"}


def find_comment(snap, post_id, text):
    for c in snap["comments"].get(post_id, []):
        if c["text"] == text:
            return c
    return None


def find_edge(snap, src, dst):
    for e in snap["edges"]:
        if e["from"] == src and e["to"] == dst:
            return e
    return None


def test_phase_privacy_and_lagged_public():
    board = PublicBoard(make_cards(), make_handles())
    sA = board.open_session("a", private_state={"memory": ["A-MARK"]})
    sB = board.open_session("b", private_state={"memory": ["B-MARK"]})
    assert sA.view()["private_state"]["memory"] == ["A-MARK"]
    assert sB.view()["private_state"]["memory"] == ["B-MARK"]

    assert sA.apply({"kind": "open", "post_id": "p1"})["status"] == "accepted"
    assert sA.apply({"kind": "follow", "handle": "@b"})["status"] == "accepted"
    assert sA.apply({"kind": "comment", "post_id": "p1", "text": "A new"})["status"] == "accepted"
    # own following visible immediately, before any commit
    v = sA.view()
    assert "@b" in v["following"]
    assert sA.apply({"kind": "finish"})["status"] == "accepted"
    board.commit_session(sA)

    snap = board.public_snapshot()
    assert snap["edges"] == []
    assert snap["followers"]["@b"] == 0

    # B opens after A commit but before advance: no A new comment yet
    assert sB.apply({"kind": "open", "post_id": "p1"})["status"] == "accepted"
    detail = sB.view()["detail"]
    texts = [c["text"] for c in detail["comments_prev"]]
    assert "A new" not in texts
    # no private markers leak into B's view
    assert "A-MARK" not in json.dumps(sB.view())

    assert sB.apply({"kind": "comment", "post_id": "p1", "text": "B new"})["status"] == "accepted"
    assert sB.apply({"kind": "finish"})["status"] == "accepted"
    board.commit_session(sB)
    board.advance()

    snap2 = board.public_snapshot()
    assert find_edge(snap2, "@a", "@b") is not None
    assert snap2["followers"]["@b"] == 1
    assert find_comment(snap2, "p1", "A new") is not None
    assert "A-MARK" not in json.dumps(snap2)
    assert "B-MARK" not in json.dumps(snap2)

    # next actor C sees recommendation toward @b
    sC = board.open_session("c", private_state={"memory": ["C-MARK"]})
    recs = sC.view()["recommendations"]
    assert any(r["handle"] == "@b" for r in recs)

    # mutating a snapshot copy cannot affect the board
    mutated = copy.deepcopy(snap2)
    mutated["edges"].append({"from": "@x", "to": "@y"})
    mutated["followers"]["@b"] = 999
    mutated["comments"]["p1"] = []
    snap3 = board.public_snapshot()
    assert find_edge(snap3, "@a", "@b") is not None
    assert snap3["followers"]["@b"] == 1
    assert find_comment(snap3, "p1", "A new") is not None


def test_order_independent_snapshot():
    def run(commit_order):
        board = PublicBoard(make_cards(), make_handles())
        sA = board.open_session("a", private_state={"memory": ["A-MARK"]})
        sB = board.open_session("b", private_state={"memory": ["B-MARK"]})
        assert sA.apply({"kind": "open", "post_id": "p1"})["status"] == "accepted"
        assert sA.apply({"kind": "follow", "handle": "@b"})["status"] == "accepted"
        assert sA.apply({"kind": "comment", "post_id": "p1", "text": "A new"})["status"] == "accepted"
        assert sA.apply({"kind": "comment", "post_id": "p1", "text": "A new2"})["status"] == "accepted"
        assert sA.apply({"kind": "finish"})["status"] == "accepted"
        assert sB.apply({"kind": "open", "post_id": "p1"})["status"] == "accepted"
        assert sB.apply({"kind": "comment", "post_id": "p1", "text": "B new"})["status"] == "accepted"
        assert sB.apply({"kind": "finish"})["status"] == "accepted"
        if commit_order == "AB":
            board.commit_session(sA)
            board.commit_session(sB)
        else:
            board.commit_session(sB)
            board.commit_session(sA)
        board.advance()
        return board.public_snapshot()

    snap_ab = run("AB")
    snap_ba = run("BA")
    assert snap_ab == snap_ba
