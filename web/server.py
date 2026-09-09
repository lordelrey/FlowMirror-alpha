"""Local companion service for the FlowMirror viewer.

The viewer works as plain static files: open `web/index.html` over any static server and
every page reads a finished run out of `runs/out/`. This service exists for the three
things static files cannot do on the author's own machine:

  * list the runs that exist, so the viewer has a picker instead of a URL you must type
  * start a run and stream the engine's stdout, so the five-step ladder shows progress
  * serve one real creative image, sha256-verified per request, so the TV arm's column
    shows the picture instead of a digest

Everything here is deliberately small and boring. It binds 127.0.0.1 and refuses any
other interface, because it launches engine subprocesses and must never be reachable off
the machine. **No credential ever passes through it**: it does not accept one, does not
log one and does not echo one. Live runs resolve their key inside the engine from
config/api.yaml, the environment, or the legacy key file; all this service reports is a
boolean saying whether a credential is configured, so the configure page can show a
read-only indicator.

    python web/server.py [--port 8765] [--images-root PATH]

Then open http://127.0.0.1:8765/web/?run=<tag>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs" / "out"
HOST = "127.0.0.1"

# One run at a time. The engine saturates the machine's cores on its own, and a second
# concurrent run would interleave its stdout into the same ladder.
STATE = {"lock": threading.Lock(), "runs": {}}
TAG_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,120}$")
IMAGE_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,160}$")


def credentials_configured() -> bool:
    """Whether a live run could find a key — never WHICH key, and never its value."""
    if (ROOT / "config" / "api.yaml").is_file():
        return True
    if (os.environ.get("FLOWMIRROR_GLM_KEY") or "").strip():
        return True
    legacy = (os.environ.get("FLOWMIRROR_LEGACY_KEY_FILE") or "").strip()
    return bool(legacy) and Path(legacy).is_file()


def safe_run_path(tag: str, rel: str) -> Path | None:
    """Resolve <runs>/<tag>/<rel>, or None if it escapes. Symlinks included.

    A path check that only rejects a literal '..' is not a path check: the resolved real
    path has to be inside runs/out/, which is what this asserts.
    """
    if not TAG_RE.match(tag or ""):
        return None
    rel = unquote(rel or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    try:
        base = RUNS.resolve()
        target = (base / tag / rel).resolve()
        target.relative_to(base)
    except (ValueError, OSError):
        return None
    return target if target.is_file() else None


def list_runs() -> list[dict]:
    out = []
    if not RUNS.is_dir():
        return out
    for d in sorted(RUNS.iterdir()):
        if not d.is_dir() or not TAG_RE.match(d.name):
            continue
        meta = d / "run_meta.json"
        entry = {
            "tag": d.name,
            "has_meta": meta.is_file(),
            "has_invariants": (d / "invariants_report.json").is_file(),
            "has_bundle": (d / "bundle" / "bundle.json").is_file(),
            "mtime": datetime.fromtimestamp(d.stat().st_mtime, timezone.utc)
                             .isoformat(timespec="seconds"),
        }
        if entry["has_meta"]:
            try:
                m = json.loads(meta.read_text(encoding="utf-8"))
                entry["status"] = m.get("status")
                entry["agents"] = (m.get("investors") or {}).get("total")
                inv = (m.get("invariants") or {}).get("summary") or {}
                entry["invariants_failed"] = inv.get("failed")
            except (OSError, ValueError):
                pass
        out.append(entry)
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out


def image_path_for(images_root: Path, image_id: str) -> Path | None:
    """The image file, only if its bytes match the digest the content pool recorded.

    Verified per request rather than once at startup: the point of the check is that a
    partially synced or re-exported store cannot silently hand the viewer a different
    picture than the one an agent was shown.
    """
    # Callers send a content sha256, never a filename: showcase bundles'
    # posts.json carries only digests so filenames cannot leak to clients.
    sha = (image_id or "").lower()
    if not SHA256_RE.match(sha):
        return None
    name = POOL_BY_SHA.get(sha)
    if not name:
        return None                      # unknown to the pool: not ours to serve
    try:
        base = images_root.resolve()
        target = (base / name).resolve()
        target.relative_to(base)
    except (ValueError, OSError):
        return None
    if not target.is_file():
        return None
    # Re-hash on every request: a partially synced or swapped pool must not
    # pass a different image off as the one the agent actually saw.
    got = hashlib.sha256(target.read_bytes()).hexdigest()
    return target if got == sha else None


def load_pool_digests() -> dict[str, str]:
    """image_id -> sha256, from the shipped content pool."""
    pool = ROOT / "data" / "creatives" / "cn" / "content_pool_demo.jsonl"
    out: dict[str, str] = {}
    if not pool.is_file():
        return out

    def as_list(v):
        if isinstance(v, list):
            return [str(x) for x in v if x is not None]
        if isinstance(v, str) and v.strip().startswith(("[", "(")):
            try:
                got = json.loads(v)
                return [str(x) for x in got] if isinstance(got, list) else []
            except ValueError:
                return []
        return [v] if isinstance(v, str) and v.strip() else []

    with pool.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            ids, shas = as_list(row.get("image_ids")), as_list(row.get("image_sha256"))
            for i, name in enumerate(ids):
                if i < len(shas):
                    out[name] = shas[i]
    return out


POOL_DIGESTS: dict[str, str] = {}
# posts.json in showcase bundles carries image sha256 digests only (filenames
# must not leak), so /api/images requests arrive as content hashes; we need
# sha -> filename to resolve them back to pool files.
POOL_BY_SHA: dict[str, str] = {}
# 64 lowercase hex digits: validates the sha argument before any lookup.
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def start_run(body: dict, images_root: Path | None) -> tuple[int, dict]:
    """Validate, then launch the engine and the exporter in a background thread."""
    with STATE["lock"]:
        if any(r["state"] == "running" for r in STATE["runs"].values()):
            return 409, {"error": "已有一次运行在进行中。等它结束再发起下一次。"}

    days = max(1, min(120, int(body.get("days") or 12)))
    agents = max(2, min(400, int(body.get("agents") or 40)))
    seed = int(body.get("seed") or 2027)
    arms = [a for a in (body.get("arms") or ["T", "TV"]) if a in ("T", "TC", "TV")]
    mock = bool(body.get("mock", True))
    if body.get("scenario", "cn") != "cn":
        return 400, {"error": "目前只有 cn 场景可运行；us 是路线图。"}
    if len(arms) < 2:
        return 400, {"error": "至少要两个臂。"}

    cfg_name = "runs/demo_three_arm.json" if len(arms) == 3 else "runs/demo_two_arm.json"
    cfg_path = ROOT / cfg_name
    if not cfg_path.is_file():
        return 400, {"error": f"找不到基底配置 {cfg_name}"}

    # Validate the merged config the way the engine will, BEFORE spending a subprocess
    # on it, so a bad request fails at step 1 with a readable message.
    try:
        sys.path.insert(0, str(ROOT))
        from flowmirror.config.validate import validate            # noqa: PLC0415
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["modality_arms"] = arms
        cfg.pop("modality_run_arm", None)
        validate(cfg, "run")
    except Exception as exc:                                        # noqa: BLE001
        return 400, {"error": f"配置校验失败：{exc}"}

    tag = f"ui_{len(arms)}arm_{agents}x{days}_s{seed}"
    out_dir = RUNS / tag
    rid = f"{int(time.time())}_{tag}"
    rec = {"id": rid, "tag": tag, "state": "running", "lines": [], "started": time.time()}
    with STATE["lock"]:
        STATE["runs"][rid] = rec

    tmp_cfg = out_dir.parent / f".{tag}.cfg.json"
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_cfg.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")

    def emit(line: str) -> None:
        rec["lines"].append(line.rstrip("\n"))

    def worker() -> None:
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        cmds = [
            [sys.executable, "-m", "flowmirror.engine.loop", str(tmp_cfg),
             *(["--mock"] if mock else []),
             "--days", str(days), "--agents", str(agents), "--seed", str(seed),
             "--out", str(out_dir)],
            [sys.executable, "-m", "flowmirror.analysis.export_bundle", str(out_dir)],
        ]
        ok = True
        for cmd in cmds:
            emit(f"[flowmirror] $ {' '.join(cmd[1:])}")
            try:
                proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True,
                                        encoding="utf-8", errors="replace", bufsize=1)
            except OSError as exc:
                emit(f"[flowmirror] FATAL 无法启动子进程：{exc}")
                ok = False
                break
            for line in proc.stdout:
                emit(line)
            if proc.wait() != 0:
                emit(f"[flowmirror] 子进程退出码 {proc.returncode}")
                ok = False
                break
        rec["state"] = "ok" if ok else "failed"
        try:
            tmp_cfg.unlink()
        except OSError:
            pass

    threading.Thread(target=worker, name=f"run-{tag}", daemon=True).start()
    return 200, {"id": rid, "tag": tag}


class Handler(SimpleHTTPRequestHandler):
    images_root: Path | None = None

    def __init__(self, *a, **kw):
        # Serving the repo root as static files shipped config/api.yaml (the
        # API key) and .git/ to anyone who could reach the port. Only web/ is
        # static now; run artifacts are exposed via the safe_run_path-backed
        # routes in do_GET instead of raw disk paths.
        super().__init__(*a, directory=str(ROOT / "web"), **kw)

    # keep the console readable: one line per request, no client address noise
    def log_message(self, fmt, *args):
        sys.stderr.write("[server] %s\n" % (fmt % args))

    def _json(self, code: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        # charset is explicit everywhere: the stdlib serves .json with no charset and
        # browsers then decode Chinese as latin-1. This project has been bitten by it.
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path) -> None:
        ctype, _ = mimetypes.guess_type(str(path))
        ctype = ctype or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/json", "application/javascript"):
            ctype += "; charset=utf-8"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def translate_path(self, path):
        # Docs and existing links point at /web/?run=<tag>, a leftover from
        # when the static root was the repo top. The static root is web/
        # itself now, so strip one leading "web" segment before delegating;
        # that keeps both /web/app.js and /app.js resolving to the same file
        # under ROOT/web/. The base translate_path keeps ownership of URL
        # decoding and ".." folding -- do not rebuild filesystem paths here.
        p = path.split("?", 1)[0].split("#", 1)[0]
        segs = p.split("/")
        if len(segs) > 1 and segs[1] == "web":
            del segs[1]
        return super().translate_path("/".join(segs))

    def do_GET(self):                                              # noqa: N802
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]

        if not self._host_ok():
            # DNS-rebinding shield: binding to 127.0.0.1 alone does not stop
            # a foreign page from resolving its own hostname to 127.0.0.1;
            # those requests arrive with a Host we never issued, so drop them.
            # Deliberately the first statement: any branch answered before it
            # (api/runs listing, artifact files, static fallback) would hand
            # a forged Host a working route.
            return self._json(403, {"error": "Host 校验失败：仅允许 127.0.0.1 / localhost（防 DNS rebinding）"})

        # web/data.js fetches artifacts at ${base}/runs/out/<tag>/<file> with
        # base defaulting to "..", i.e. relative to the old static root. The
        # static root is web/ now, so those URLs no longer map to files; route
        # them through safe_run_path (same traversal guards as /api/runs/...)
        # to keep the UI working without re-exposing the repo root.
        if len(parts) >= 4 and parts[:2] == ["runs", "out"]:
            p = safe_run_path(parts[2], "/".join(parts[3:]))
            if p is None:
                return self._json(404, {"error": "找不到该运行产物，或路径越界"})
            return self._file(p)

        if not parts or parts[0] != "api":
            return super().do_GET()

        if parts == ["api", "runs"]:
            return self._json(200, {
                "runs": list_runs(),
                "credentials_configured": credentials_configured(),
                "images_root": bool(self.images_root),
            })

        if len(parts) >= 4 and parts[:2] == ["api", "runs"]:
            p = safe_run_path(parts[2], "/".join(parts[3:]))
            if p is None:
                return self._json(404, {"error": "找不到该运行产物，或路径越界"})
            return self._file(p)


        if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "log":
            rid = unquote(parts[2])
            rec = STATE["runs"].get(rid)
            if not rec:
                return self._json(404, {"error": "未知的运行 id"})
            since = 0
            try:
                since = max(0, int(parse_qs(u.query).get("since", ["0"])[0]))
            except ValueError:
                pass
            lines = rec["lines"][since:]
            return self._json(200, {
                "state": rec["state"], "tag": rec["tag"],
                "lines": lines, "next": since + len(lines),
            })

        if len(parts) == 3 and parts[:2] == ["api", "images"]:
            if not self.images_root:
                return self._json(404, {
                    "error": "本服务未以 --images-root 启动，所以不提供真图。"
                             "模态页会显示摘要占位说明。",
                })
            p = image_path_for(self.images_root, unquote(parts[2]))
            if p is None:
                return self._json(404, {
                    "error": "该 sha256 不属于内容池；或库中对应文件与该 sha256 不符（图库与内容池不同步，两种情况均拒绝提供）",
                })
            return self._file(p)

        return self._json(404, {"error": "未知的 API 端点"})

    def _host_ok(self):
        # Binding to 127.0.0.1 alone does not stop DNS rebinding: a page on
        # some other site can resolve its own hostname to 127.0.0.1, and the
        # browser then reaches us with a Host we never issued. Only requests
        # that address this server by 127.0.0.1 / localhost (any port) pass.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip().lower()
        return host in ("127.0.0.1", "localhost")

    def do_POST(self):                                             # noqa: N802
        if not self._host_ok():
            return self._json(403, {"error": "Host 校验失败：仅允许 127.0.0.1 / localhost（防 DNS rebinding）"})
        u = urlparse(self.path)
        if u.path.rstrip("/") != "/api/run":
            return self._json(404, {"error": "未知的 API 端点"})
        # Starting a run is an irreversible, cross-system action (subprocess,
        # disk writes). A plain HTML form with enctype="text/plain" can carry
        # JSON-shaped bodies and is NOT covered by the CORS preflight that
        # guards cross-origin JSON fetches, so gate on both headers below.
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return self._json(400, {"error": "Content-Type 必须是 application/json；不接受 text/plain 表单直发"})
        origin = self.headers.get("Origin")
        if origin is not None:
            # Browsers attach Origin to same-origin POSTs too; absent Origin
            # (curl, same-origin navigation) is allowed through.
            host = self.headers.get("Host") or ""
            if origin not in ("http://" + host, "https://" + host):
                return self._json(403, {"error": "拒绝跨源请求：Origin 不是本服务自身"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except (ValueError, UnicodeDecodeError) as exc:
            return self._json(400, {"error": f"请求体不是合法 JSON：{exc}"})
        if not isinstance(body, dict):
            return self._json(400, {"error": "请求体必须是一个对象"})
        # A key in the request body is a mistake worth naming rather than ignoring.
        for k in body:
            if re.search(r"key|token|secret|password", str(k), re.I):
                return self._json(400, {
                    "error": "本服务不接受任何密钥字段。凭据只在引擎内部解析。",
                })
        try:
            code, payload = start_run(body, self.images_root)
        except (TypeError, ValueError) as exc:
            return self._json(400, {"error": f"参数不合法：{exc}"})
        return self._json(code, payload)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--images-root", default=None,
                    help="本机创意图片库目录。给了才提供 /api/images/<id>，"
                         "且每次请求都重新校验 sha256。图片永不进仓库。")
    args = ap.parse_args(argv)

    global POOL_DIGESTS, POOL_BY_SHA
    POOL_DIGESTS = load_pool_digests()
    # Reverse index for /api/images: lookups come in as content hashes because
    # bundles never carry filenames. POOL_DIGESTS keeps its shape for existing
    # users (e.g. the startup count).
    POOL_BY_SHA = {sha.lower(): name for name, sha in POOL_DIGESTS.items()}

    root = None
    if args.images_root:
        root = Path(args.images_root).expanduser()
        if not root.is_dir():
            print(f"[FATAL] --images-root 不是一个目录：{root}", file=sys.stderr)
            return 1
        try:
            root.resolve().relative_to(ROOT.resolve())
            print("[FATAL] --images-root 指向仓库内部。图片永不进仓库；"
                  "把库放在仓库之外。", file=sys.stderr)
            return 1
        except ValueError:
            pass                                   # outside the repo, which is required
        print(f"[server] images: {len(POOL_DIGESTS)} 条池内摘要，库 {root}")
    else:
        print("[server] 未配 --images-root：不提供真图，模态页显示摘要占位")

    Handler.images_root = root
    httpd = ThreadingHTTPServer((HOST, args.port), Handler)
    print(f"[server] 只监听 {HOST}:{args.port}（本服务会启动引擎子进程，绝不对外开放）")
    print(f"[server] 打开 http://{HOST}:{args.port}/web/")
    print(f"[server] 凭据已配置：{credentials_configured()}（本服务不接受、不记录、不回显密钥）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] 已停止")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
