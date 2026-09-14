"""Offline browsing probes and exact replay; this CLI never opens an LLM client."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from flowmirror.platform.browse_run import replay_run, run_browsing
from flowmirror.platform.browse_store import read_checkpoint


def demo_spec(*, null=False):
    return {
        "name": "cold-start-mechanism-demo", "phases": 4, "max_steps": 6, "page_size": 2,
        "policy_mode": "null" if null else "scripted",
        "policy_name": "finish-only-v1" if null else "cold-start-script-v1",
        "agents": [
            {"id": "reader_a", "handle": "@reader_a", "arm": "T",
             "private_state": {"persona": "演练读者 A", "cash": 10000, "memory": ["A的私有便签"]}},
            {"id": "author_b", "handle": "@author_b", "arm": "TC",
             "private_state": {"persona": "演练评论者 B", "cash": 20000, "memory": ["B的私有便签"]}},
            {"id": "reader_c", "handle": "@reader_c", "arm": "TV",
             "private_state": {"persona": "演练读者 C", "cash": 15000, "memory": ["C的私有便签"]}},
        ],
        "cards": [
            {"post_id": "p1", "org": "虚构机构甲", "title": "收益曲线之外，还看什么？",
             "caption": "离线素材：历史收益不保证未来收益。讨论回撤与费用，不提供真实投资建议。",
             "ocr_text": "演练图片文字：风险、费用、期限", "image_caption_frozen": "虚构图示说明",
             "comments_prev": []},
            {"post_id": "p2", "org": "虚构机构乙", "title": "短期波动与持有期限",
             "caption": "仅用于测试翻页、收藏及评论动作的虚构素材。", "comments_prev": []},
            {"post_id": "p3", "org": "虚构机构丙", "title": "定投费用如何比较",
             "caption": "脚本演练素材，不代表市场实时数据。", "comments_prev": []},
        ],
    }


def scripted_policy(view):
    """Mechanism exercise, NOT evidence of autonomous model following."""
    phase, actor, step = view["phase"], view["agent_id"], view["step"]
    open_post = {"kind": "open", "post_id": "p1"}
    comment = {"kind": "comment", "post_id": "p1",
               "text": "演练评论：我会同时看回撤与费用。" if actor == "author_b"
               else "演练评论：想了解这个判断依据。"}
    follow = {"kind": "follow", "handle": "@author_b"}
    if phase == 0:
        plan = [open_post, comment]
    elif phase == 1 and actor == "reader_a":
        plan = [open_post, follow, {"kind": "like", "post_id": "p1"}, {"kind": "scroll"}]
    elif phase == 2 and actor == "reader_c":
        plan = [follow, open_post, {"kind": "save", "post_id": "p1"}]
    elif phase == 3 and actor == "reader_c":
        plan = [open_post, {"kind": "unfollow", "handle": "@author_b"}]
    else:
        plan = [open_post, comment] if actor == "author_b" else [open_post]
    return plan[step] if step < len(plan) else {"kind": "finish"}


def timed_demo_spec():
    """Synthetic CN/US observations with explicit delayed publication, not live prices."""
    spec = demo_spec()
    spec["name"] = "timed-cn-us-synthetic-demo"
    spec["phase_times"] = ["2026-09-08T09:00:00+08:00", "2026-09-08T15:00:00+08:00",
                           "2026-09-09T04:00:00+08:00", "2026-09-09T21:00:00+08:00"]
    for agent in spec["agents"]:
        agent["market"] = "US" if agent["id"] == "reader_c" else "CN"
    spec["cards"][1]["published_at"] = "2026-09-08T12:00:00+08:00"
    spec["cards"][2]["published_at"] = "2026-09-08T20:00:00+08:00"
    spec["market_data"] = [
        {"instrument_id": "CN_DEMO_FUND", "market": "CN", "currency": "CNY", "kind": "fund_nav",
         "observed_at": "2026-09-07T15:00:00+08:00", "available_at": "2026-09-07T20:00:00+08:00",
         "price": 1.0, "source": "synthetic_fixture", "synthetic": True},
        {"instrument_id": "CN_DEMO_FUND", "market": "CN", "currency": "CNY", "kind": "fund_nav",
         "observed_at": "2026-09-08T15:00:00+08:00", "available_at": "2026-09-08T20:30:00+08:00",
         "price": 1.01, "source": "synthetic_fixture", "synthetic": True},
        {"instrument_id": "US_DEMO_ETF", "market": "US", "currency": "USD", "kind": "exchange_price",
         "observed_at": "2026-09-04T16:00:00-04:00", "available_at": "2026-09-04T16:00:01-04:00",
         "price": 100.0, "source": "synthetic_fixture", "synthetic": True},
        {"instrument_id": "US_DEMO_ETF", "market": "US", "currency": "USD", "kind": "exchange_price",
         "observed_at": "2026-09-08T16:00:00-04:00", "available_at": "2026-09-08T16:00:01-04:00",
         "price": 101.0, "source": "synthetic_fixture", "synthetic": True},
    ]
    return spec


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="scripted cold-start mechanism test")
    mode.add_argument("--timed-demo", action="store_true", help="synthetic CN/US tape and scheduled posts")
    mode.add_argument('--account-demo', action='store_true', help='synthetic browsing, orders, and delayed settlement')
    mode.add_argument('--marketing-demo', action='store_true',
                      help='synthetic CN/US institution RULE feedback, browsing, and simulated orders')
    mode.add_argument("--null", action="store_true", help="all actors finish without interaction")
    mode.add_argument("--resume", action="store_true", help="resume an offline saved probe")
    mode.add_argument("--replay", action="store_true", help="read-only exact cached replay")
    mode.add_argument("--spec", type=Path, help="JSON spec using a supported offline policy")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument('--storage', choices=['journal', 'snapshot'], default='journal',
                        help='new-run storage; resumes preserve the saved format')
    parser.add_argument("--max-new-calls", type=int, default=1000,
                        help="policy invocation limit; these offline policies make zero LLM calls")
    args = parser.parse_args(argv)
    if args.replay:
        result = replay_run(args.out)
        print("identical=" + str(result["identical"]), flush=True)
    else:
        if args.resume:
            spec = read_checkpoint(args.out)["spec"]
        elif args.spec:
            with args.spec.open(encoding="utf-8") as stream:
                spec = json.load(stream)
        else:
            if args.marketing_demo:
                from flowmirror.platform.marketing_demo import marketing_demo_spec
                spec = marketing_demo_spec()
            elif args.account_demo:
                from flowmirror.platform.broker_demo import account_demo_spec
                spec = account_demo_spec()
            else:
                spec = timed_demo_spec() if args.timed_demo else demo_spec(null=args.null)
        pair = (spec.get("policy_mode"), spec.get("policy_name"))
        if pair == ("scripted", "cold-start-script-v1"):
            policy = scripted_policy
        elif pair == ('scripted', 'account-script-v1'):
            from flowmirror.platform.broker_demo import account_policy
            policy = account_policy
        elif pair == ('scripted', 'marketing-rule-v1'):
            from flowmirror.platform.marketing_demo import marketing_policy
            policy = marketing_policy
        elif pair == ('scripted', 'warehouse-open-finish-v1'):
            from flowmirror.platform.warehouse import warehouse_policy
            policy = warehouse_policy
        elif pair == ("null", "finish-only-v1"):
            policy = lambda view: {"kind": "finish"}
        else:
            parser.error("this CLI accepts only named offline policies; external models require explicit integration")
        result = run_browsing(spec, args.out, policy, max_new_calls=args.max_new_calls,
                             storage=args.storage)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 2 if result.get("status") == "uncertain" else 0


if __name__ == "__main__":
    raise SystemExit(main())
