import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "script"))

import run_social_probe as drv


def make_base():
    return {
        "_what": "old",
        "run_tag": "main_ref",
        "out_dir": "runs/out/main_ref",
        "model": "gpt-x",
        "modality": "chat",
        "seed": 2027,
        "n_agents": 25,
        "window": {"max_trading_days": 20, "warmup": 2},
        "social_graph": {"enabled": False, "kind": "random", "p": 0.1},
        "llm": {"cache": "runs/out/main_ref/llm_cache.jsonl", "retries": 2},
        "stimuli": [{"day": 1, "text": "hi"}],
        "suitability": {"min_age": 18},
    }


class TestBuildDerivedConfig:
    def test_changes_exactly_seven_locations_and_preserves_rest(self):
        base = make_base()
        snapshot = json.loads(json.dumps(base))
        derived = drv.build_derived_config(base)

        assert derived["_what"] == (
            "Real-model probe of the influencer layer after cards E7/E8A/E8B."
        )
        assert derived["run_tag"] == "probe_social_100x5"
        assert derived["out_dir"] == "runs/out/probe_social_100x5"
        assert derived["n_agents"] == 100
        assert derived["window"]["max_trading_days"] == 5
        assert derived["window"]["warmup"] == 2
        assert derived["social_graph"] == {"enabled": True}
        assert derived["llm"]["cache"] == (
            "runs/out/probe_social_100x5/llm_cache.jsonl"
        )
        assert derived["llm"]["retries"] == 2
        assert derived["model"] == base["model"]
        assert derived["modality"] == base["modality"]
        assert derived["seed"] == base["seed"]
        assert derived["stimuli"] == base["stimuli"]
        assert derived["suitability"] == base["suitability"]
        # input untouched
        assert base == snapshot
        # only the seven keys/subkeys differ
        assert derived is not base

    def test_all_differences_are_only_the_specified_ones(self):
        base = make_base()
        derived = drv.build_derived_config(base)
        for key in set(base) | set(derived):
            if key in ("_what", "run_tag", "out_dir", "n_agents",
                       "social_graph", "window", "llm"):
                continue
            assert derived[key] == base[key], key
        assert derived["window"] == {**base["window"], "max_trading_days": 5}
        assert derived["llm"] == {**base["llm"],
                                  "cache": "runs/out/probe_social_100x5/llm_cache.jsonl"}


class TestEnsureDerivedConfig:
    def test_written_when_absent(self, tmp_path):
        base = make_base()
        desired = drv.build_derived_config(base)
        p = tmp_path / "derived.json"
        assert drv.ensure_derived_config(tmp_path / "base.json", p, desired) == "written"
        text = p.read_text(encoding="utf-8")
        assert text.endswith("\n") and not text.endswith("\n\n")
        assert json.loads(text) == desired
        assert drv.ensure_derived_config(tmp_path / "b.json", p, desired) == "reused"

    def test_equal_existing_reuses_untouched(self, tmp_path):
        desired = drv.build_derived_config(make_base())
        p = tmp_path / "derived.json"
        p.write_text(json.dumps(desired, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
        before = p.read_bytes()
        assert drv.ensure_derived_config(tmp_path / "b.json", p, desired) == "reused"
        assert p.read_bytes() == before

    def test_different_existing_rejects_without_overwrite(self, tmp_path):
        desired = drv.build_derived_config(make_base())
        p = tmp_path / "derived.json"
        p.write_text(json.dumps({"different": True}, indent=2) + "\n",
                     encoding="utf-8")
        before = p.read_bytes()
        with pytest.raises(drv.SafetyError):
            drv.ensure_derived_config(tmp_path / "b.json", p, desired)
        assert p.read_bytes() == before

    def test_malformed_existing_rejects_without_overwrite(self, tmp_path):
        desired = drv.build_derived_config(make_base())
        p = tmp_path / "derived.json"
        p.write_text("{not json", encoding="utf-8")
        before = p.read_bytes()
        with pytest.raises(drv.SafetyError):
            drv.ensure_derived_config(tmp_path / "b.json", p, desired)
        assert p.read_bytes() == before


class TestSummarizeEvents:
    def test_exact_counts(self):
        lines = [
            json.dumps({"ev": "st", "what": "follow_user", "i": "u1"}),
            json.dumps({"ev": "st", "what": "follow_user", "i": "u1"}),  # dup follower
            json.dumps({"ev": "st", "what": "follow_user", "i": "u2"}),
            json.dumps({"ev": "st", "what": "other", "i": "u9"}),
            json.dumps({"ev": "dec", "p_follow_users": ["h1"],
                        "violations": ["unknown_handle", "unknown_handle"]}),
            json.dumps({"ev": "dec", "p_follow_users": [],
                        "violations": ["unknown_handle"]}),
            json.dumps({"ev": "dec", "p_follow_users": ["h2"], "violations": []}),
            "not json at all",
            json.dumps(["a", "non-dict", "row"]),
            "",
        ]
        s = drv.summarize_events(lines)
        assert s["follow_edges"] == 3
        assert s["unique_followers"] == 2
        assert s["decisions"] == 3
        assert s["nonempty_intent"] == 2
        assert s["unknown_handle_occurrences"] == 3
        assert s["malformed_rows"] == 2

    def test_zero_edges_is_zero(self):
        assert drv.summarize_events([])["follow_edges"] == 0


class FakeRunner:
    """Callable runner substitute with recordable results."""

    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return SimpleNamespace(returncode=self.returncode,
                               stdout=self.stdout, stderr="")


class TestInspectPythonCollisions:
    def _mk(self, rc=0, stdout=""):
        return FakeRunner(rc, stdout)

    def test_nonzero_raises(self):
        with pytest.raises(drv.SafetyError):
            drv.inspect_python_collisions(runner=self._mk(rc=1),
                                          platform_name="Windows")

    def test_noninteger_ok_stdout_means_zero(self):
        # stdout lacking integer crash: returncode 0 with unrelated lines -> 0
        n = drv.inspect_python_collisions(
            runner=self._mk(stdout="python.exe\tsome other script.py"),
            platform_name="Windows")
        assert n == 0

    def test_positive_integer_detectable(self):
        stdout = (
            "python.exe\tpython -m flowmirror.engine.loop cfg.json\n"
            "python.exe\tpython -m flowmirror.engine.loop cfg2.json\n"
            "python.exe\tpython script/run_grid.py --x\n"
            "python.exe\tpython unrelated.py\n"
        )
        n = drv.inspect_python_collisions(runner=self._mk(stdout=stdout),
                                          platform_name="Windows")
        assert n == 3

    def test_windows_backslash_grid_path_detected(self):
        n = drv.inspect_python_collisions(
            runner=self._mk(stdout="python.exe\tpython script\\run_grid.py\n"),
            platform_name="Windows")
        assert n == 1

    def test_malformed_nonempty_line_fails_closed(self):
        with pytest.raises(drv.SafetyError):
            drv.inspect_python_collisions(
                runner=self._mk(stdout="python.exe python script\\run_grid.py\n"),
                platform_name="Windows")

    def test_non_windows_fails_closed(self):
        with pytest.raises(drv.SafetyError):
            drv.inspect_python_collisions(runner=self._mk(),
                                          platform_name="Linux")


def test_real_mode_safety_refusal_is_concise(monkeypatch, tmp_path, capsys):
    def boom():
        raise drv.SafetyError("grid active")

    base = tmp_path / "base.json"
    derived = tmp_path / "derived.json"
    output = tmp_path / "output"
    log = tmp_path / "log.txt"
    base.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(drv, "require_no_collisions", boom)
    monkeypatch.setattr(drv, "BASE_CONFIG_PATH", base)
    monkeypatch.setattr(drv, "DERIVED_CONFIG_PATH", derived)
    monkeypatch.setattr(drv, "OUT_DIR_PATH", output)
    monkeypatch.setattr(drv, "LOG_PATH", log)

    rc = drv.main([])

    assert rc == 2
    assert not derived.exists()
    assert not output.exists()
    assert not log.exists()
    err = capsys.readouterr().err
    assert "refused before launch" in err
    assert "grid active" in err


def test_second_collision_check_race_refuses_before_paid_launch(
        tmp_path, monkeypatch, capsys):
    base = tmp_path / "base.json"
    base.write_text(json.dumps(make_base()), encoding="utf-8")

    monkeypatch.setattr(drv, "BASE_CONFIG_PATH", base)
    monkeypatch.setattr(drv, "DERIVED_CONFIG_PATH", tmp_path / "derived.json")
    monkeypatch.setattr(drv, "OUT_DIR_PATH", tmp_path / "out")
    monkeypatch.setattr(drv, "LOG_PATH", tmp_path / "log.txt")

    calls = {"collisions": 0, "commands": []}

    def fake_require_no_collisions():
        calls["collisions"] += 1
        if calls["collisions"] == 2:
            raise drv.SafetyError("grid appeared")

    def fake_run_command(cmd, *args, **kwargs):
        calls["commands"].append(list(cmd))
        return 0, ""

    monkeypatch.setattr(drv, "require_no_collisions", fake_require_no_collisions)
    monkeypatch.setattr(drv, "run_command", fake_run_command)

    rc = drv.main([])

    assert rc == 2
    assert calls["collisions"] == 2
    assert len(calls["commands"]) == 1
    cmd = calls["commands"][0]
    assert any("flowmirror.cli" in part for part in cmd)
    assert "validate" in cmd

    err = capsys.readouterr().err
    assert "refused before paid launch" in err
    assert "grid appeared" in err

    log_path = tmp_path / "log.txt"
    assert log_path.exists()
    assert "refused before paid launch: grid appeared" in log_path.read_text(
        encoding="utf-8"
    )


class TestDryRun:
    def test_dry_run_writes_nothing_and_runs_no_subprocess(
            self, tmp_path, monkeypatch, capsys):
        base = make_base()
        base_path = tmp_path / "runs" / "main_ref_s2027.json"
        base_path.parent.mkdir(parents=True)
        base_path.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")

        monkeypatch.setattr(drv, "BASE_CONFIG_PATH", base_path)
        monkeypatch.setattr(drv, "DERIVED_CONFIG_PATH",
                            tmp_path / "runs" / "probe_social_100x5.json")
        monkeypatch.setattr(drv, "OUT_DIR_PATH",
                            tmp_path / "runs" / "out" / "probe_social_100x5")
        monkeypatch.setattr(drv, "LOG_PATH",
                            tmp_path / "runs" / "out" / "probe.log")

        calls = []
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: calls.append((a, k)) or SimpleNamespace(
                returncode=0, stdout="", stderr=""))

        rc = drv.main(["--dry-run"])
        assert rc == 0
        assert calls == []  # no subprocess.run whatsoever
        assert not (tmp_path / "runs" / "probe_social_100x5.json").exists()
        assert not (tmp_path / "runs" / "out").exists()
        out = capsys.readouterr().out
        assert "flowmirror.cli validate" in out
        assert "flowmirror.engine.loop" in out
        assert "--workers 6" in out
        assert "--replay-check" in out
        assert "flowmirror.analysis.influence" in out
