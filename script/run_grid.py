"""Multi-day unattended grid driver for FlowMirror v7.

Runs the sixteen preregistered tags one at a time: engine run, then a
free replay check (on-disk LLM cache, zero paid calls), then analysis.

Concurrency guard: today two engine instances wrote to the same output
directory at once (~247 paid calls wasted, event log corrupted), so
before every launch the driver counts live engine processes via WMI
and exits (code 2) if any is found; one engine at a time is job #1.

Resume: after any interruption just re-run this script. Completed tags
(status "ok" in runs/out/<tag>/run_meta.json) are skipped and any other
existing non-ok report is rerun; unfinished tags replay
for free from the engine's LLM cache. Nothing under runs/out/ is ever
deleted or overwritten (logs append-only); no auto-retry, since retrying
during a rate-limit window only burns quota.
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import time

ORDER = [
    "main_ref_s2027", "main_ref_s3031", "main_ref_s4049", "main_ref_s5057", "main_ref_s6071",
    "main_nosuit_s2027", "main_nosuit_s3031", "main_nosuit_s4049", "main_nosuit_s5057", "main_nosuit_s6071",
    "heat_seed_s2027", "heat_seed_s3031", "heat_seed_s4049",
    "emerge_s2027", "emerge_s3031", "emerge_s4049",
]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(ROOT, "runs")
OUT_DIR = os.path.join(RUNS_DIR, "out")

# WMI probe counting live engine processes. "-ne $PID" excludes the probe
# itself: powershell.exe's own command line contains the pattern, so the
# verbatim probe would always report >= 1 and block every launch.
PS_COUNT = ("(Get-CimInstance Win32_Process | Where-Object { "
            "$_.CommandLine -like '*flowmirror.engine.loop*' -and "
            "$_.ProcessId -ne $PID }).Count")

ANALYSIS_MODULES = ("society_metrics", "sell_side", "disposition", "influence",
                    "institutions", "social_proof", "compliance")

# Engine exit codes other than 0 (0 = clean finish).
ENGINE_EXIT_MEANING = {2: "预算上限停止", 3: "决策失败停机或回放不一致",
                       4: "一致性检查失败"}

REPLAY_OK_MARKER = "replay-check identical=True"

_DRIVER_LOG = None


def log(message):
    """Print one driver line to stdout and append it to the driver log."""
    line = "%s %s" % (datetime.datetime.now().strftime("[%H:%M:%S]"), message)
    print(line, flush=True)
    if _DRIVER_LOG:
        with open(_DRIVER_LOG, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def log_tail(path, count=20):
    """Echo the last non-empty lines of a log via a cheap tail read."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            handle.seek(max(0, size - 65536))
            chunk = handle.read().decode("utf-8", "replace")
        lines = [item for item in chunk.splitlines() if item.strip()][-count:]
    except OSError:
        lines = ["<无法读取日志文件: %s>" % path]
    for item in lines:
        log("  | %s" % item)


def count_cache_lines(path):
    """Count non-empty lines of llm_cache.jsonl; -1 when it is absent."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return -1


def count_artifacts(path):
    """Count files under the analysis output directory."""
    return sum(len(files) for _dir, _sub, files in os.walk(path))


def _run_meta_status(out_dir):
    """Return run_meta status: None if absent, "unreadable" if malformed, else status."""
    path = os.path.join(out_dir, "run_meta.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return "unreadable"
    if not isinstance(data, dict):
        return "unreadable"
    status = data.get("status")
    if status is None:
        return "missing-status"
    return str(status)


def run_meta_is_complete(out_dir):
    """True only when run_meta.json reports status == "ok"."""
    return _run_meta_status(out_dir) == "ok"


def count_running_engines(runner=subprocess.run, platform_name=None):
    """Return the number of live engine processes; on Windows, fail closed."""
    if platform_name is None:
        platform_name = os.name
    if platform_name != "nt":
        log("非 Windows 平台，跳过并跑检查（无 WMI），请自行确认没有引擎在跑。")
        return 0
    try:
        probe = runner(["powershell", "-NoProfile", "-Command", PS_COUNT],
                       capture_output=True, text=True, errors="replace")
        if probe.returncode != 0:
            raise RuntimeError("powerShell 探测失败，rc=%d" % probe.returncode)
        raw = (probe.stdout or "").strip()
        if not raw:
            raise RuntimeError("powerShell 探测输出为空。")
        try:
            return int(raw)
        except ValueError:
            raise RuntimeError("powerShell 探测输出非整数：%r" % raw)
    except OSError as exc:
        raise RuntimeError("并跑检查无法执行：%r" % exc)


def engine_command(cfg, out, workers, extra=()):
    """Build the engine command line; always executed with cwd=ROOT."""
    return ([sys.executable, "-m", "flowmirror.engine.loop", cfg, "--workers",
             str(workers), "--out", out] + list(extra))


def run_analysis_module(command, tag_log):
    """Run one analysis module; failure warns but never stops the grid."""
    try:
        proc = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              errors="replace")
    except OSError as exc:
        log("警告：分析模块 %s 无法启动（%s），可事后重跑。" % (command[2], exc))
        return
    with open(tag_log, "a", encoding="utf-8") as handle:
        handle.write(proc.stdout or "")
    log("分析模块 %s %s。" % (command[2], "完成" if proc.returncode == 0 else
        "退出码 %d（不停车；分析层不产生付费调用，可事后重跑）" % proc.returncode))


def dry_run_report(tag, args):
    """Dry run: print the skip decision and the command; start nothing."""
    cfg = os.path.join(RUNS_DIR, tag + ".json")
    out = os.path.join(OUT_DIR, tag)
    status = _run_meta_status(out)
    if status == "ok":
        log("[dry-run] %s：已完成（run_meta.json status=ok），将跳过。" % tag)
        return
    if status is not None:
        log("[dry-run] %s：run_meta.json 存在但 status=%s，将重跑。" % (tag, status))
    if not os.path.isfile(cfg):
        log("[dry-run] %s：配置缺失 %s（真实运行时会在此停下）。" % (tag, cfg))
    else:
        state = "未开始" if not os.path.isdir(out) else "未完成"
        log("[dry-run] %s：%s，将运行：%s（随后 --replay-check 与分析层）"
            % (tag, state, " ".join(engine_command(cfg, out, args.workers))))


def process_tag(tag, args):
    """Run one tag end to end; returns "skipped"/"ok"; exits when fatal."""
    cfg = os.path.join(RUNS_DIR, tag + ".json")
    out = os.path.join(OUT_DIR, tag)
    tag_log = os.path.join(OUT_DIR, tag + "_driver.log")
    started = time.time()
    # Step 1: skip completed tags (idempotent resume).
    status = _run_meta_status(out)
    if status == "ok":
        log("跳过 %s：runs/out/%s/run_meta.json status=ok，视为已完成。" % (tag, tag))
        return "skipped"
    elif status is not None:
        log("tag=%s：run_meta.json 存在但 status=%s（非 ok），将重跑。" % (tag, status))
    if not os.path.isfile(cfg):
        log("致命：配置文件不存在 %s，停止整个网格（不重试）。" % cfg)
        sys.exit(1)
    # Step 2: concurrency guard -- the reason this driver exists.
    try:
        engines = count_running_engines()
    except RuntimeError as exc:
        log("tag=%s：并跑检查失败（%s），拒绝启动引擎。" % (tag, exc))
        sys.exit(2)
    if engines > 0:
        log("并跑保护触发：检测到 %d 个引擎进程已在运行，tag=%s 被拦下。" % (engines, tag))
        log("先确认没有引擎在跑再重来。")
        sys.exit(2)
    # Step 3: the paid run itself; no timeout (10+ hour runs are normal).
    command = engine_command(cfg, out, args.workers)
    log("启动引擎：%s" % " ".join(command))
    with open(tag_log, "a", encoding="utf-8") as handle:
        proc = subprocess.run(command, cwd=ROOT, stdout=handle, stderr=handle)
    # Step 4: branch on the engine's documented exit codes.
    if proc.returncode != 0:
        meaning = ENGINE_EXIT_MEANING.get(proc.returncode, "未知含义")
        log("引擎异常退出：tag=%s 退出码=%d（%s）。停止整个网格：不重试、不跳到下一个。"
            % (tag, proc.returncode, meaning))
        log_tail(tag_log, 20)
        sys.exit(3)
    log("引擎正常结束：tag=%s。" % tag)
    # Step 5: free replay consistency check (served from the LLM cache).
    replay_cmd = engine_command(cfg, out, args.workers, extra=["--replay-check"])
    log("回放校验（免费，走缓存）：%s" % " ".join(replay_cmd))
    replay = subprocess.run(replay_cmd, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            errors="replace")
    with open(tag_log, "a", encoding="utf-8") as handle:
        handle.write(replay.stdout or "")
    if replay.returncode != 0 or REPLAY_OK_MARKER not in (replay.stdout or ""):
        log("致命：回放校验未确认一致（tag=%s，退出码=%d，未见 %s）。停止整个网格。"
            % (tag, replay.returncode, REPLAY_OK_MARKER))
        log_tail(tag_log, 20)
        sys.exit(4)
    log("回放校验通过：%s。" % REPLAY_OK_MARKER)
    # Step 6: analysis layer; warning-only failures, re-runnable for free.
    log("运行分析层：modality 落盘 + %d 个打印模块。" % len(ANALYSIS_MODULES))
    run_analysis_module([sys.executable, "-m", "flowmirror.analysis.modality", out, "--out",
                         os.path.join(out, "analysis", "modality.json")], tag_log)
    for module in ANALYSIS_MODULES:
        run_analysis_module([sys.executable, "-m", "flowmirror.analysis." + module, out], tag_log)
    # Step 7: one-line summary for this tag.
    cache = count_cache_lines(os.path.join(out, "llm_cache.jsonl"))
    cache_text = "缓存文件缺失" if cache < 0 else "缓存 %d 行" % cache
    log("小结 tag=%s：总耗时 %.1f 分钟；%s；分析产物 %d 个。"
        % (tag, (time.time() - started) / 60.0, cache_text,
           count_artifacts(os.path.join(out, "analysis"))))
    return "ok"


def build_parser():
    """CLI parser for the driver."""
    parser = argparse.ArgumentParser(
        description="FlowMirror v7 多日无人值守网格驱动（顺序单引擎，带并跑保护）。")
    parser.add_argument("--workers", type=int, default=6, help=(
        "LLM 并发数，默认 6：实测 6 并发 367 次/小时，10 并发反而掉到 91 并触发账户级限流。"))
    parser.add_argument("--only", action="append", metavar="TAG",
                        help="只跑指定 tag，可重复给出；执行顺序仍按 ORDER。")
    parser.add_argument("--stop-after", type=int, default=0,
                        help="成功跑完 N 个 tag 后正常退出（默认 0 表示不限）。")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印将要执行的命令与跳过判断，不启动任何进程、不做并跑检查。")
    parser.add_argument("--gap", type=int, default=60,
                        help="相邻两次引擎运行之间的等待秒数（默认 60），给限流窗口留恢复时间。")
    parser.add_argument("--log", default=os.path.join(OUT_DIR, "run_grid_driver.log"),
                        help="驱动自身日志文件（追加模式，默认 runs/out/run_grid_driver.log）。")
    return parser


def main(argv=None):
    global _DRIVER_LOG
    args = build_parser().parse_args(argv)
    _DRIVER_LOG = os.path.abspath(args.log)
    os.makedirs(os.path.dirname(_DRIVER_LOG) or ".", exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    tags = ORDER
    if args.only:
        unknown = sorted(set(args.only) - set(ORDER))
        if unknown:
            log("致命：--only 含未知 tag：%s（合法值见 ORDER）。" % ", ".join(unknown))
            return 1
        tags = [item for item in ORDER if item in set(args.only)]
    mode = "dry-run（不启动任何进程）" if args.dry_run else "真实运行"
    log("run_grid 启动：%s；本次计划 %d 个 tag；workers=%d gap=%ds stop-after=%s。"
        % (mode, len(tags), args.workers, args.gap, args.stop_after or "不限"))
    completed = 0
    prev_ran_engine = False
    for tag in tags:
        if args.dry_run:
            dry_run_report(tag, args)
            continue
        if prev_ran_engine and args.gap > 0:
            log("按 --gap=%ds 等待，给提供商限流窗口留恢复时间……" % args.gap)
            time.sleep(args.gap)
        status = process_tag(tag, args)  # may sys.exit() on fatal errors
        prev_ran_engine = (status == "ok")
        if status == "ok":
            completed += 1
            if args.stop_after > 0 and completed >= args.stop_after:
                log("已成功完成 %d 个 tag，达到 --stop-after，正常退出。" % completed)
                return 0
    log("全部计划内 tag 处理完毕，正常退出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
