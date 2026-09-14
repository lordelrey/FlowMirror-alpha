"""Community trace viewer: read-only, stdlib-only local HTTP service.

Serves the community.html/community.js UI and a small JSON API backed by
RunObserver summaries of completed runs only. This is a research/inspection
tool, NOT an agent tool: it must never be exposed to agents, and
JsonBrowsePolicy-style agent code consumes only BrowseSession.view, never
these observer URLs. No credentials, no POST-driven launches, no engine or
model loading from this server.

Usage:
    python web/community_server.py --source-root PATH --images-root PATH --port 8793
"""

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = CODE_ROOT / "web"
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))  # code repo only; source_root stays off the path

HOST = "127.0.0.1"  # hardcoded
DEFAULT_PORT = 8793
RUN_TAG_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
FRAME_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
POOL_FILENAME = "content_pool_v1_captioned_v2.jsonl"
_IMAGE_LOCK = threading.Lock()  # module-global: shared by ALL server instances


def _load_pool_map(source_root):
    """Map image_sha256 -> image filename string from the pool.

    Each pool row carries image_sha256 as a LIST and image_ids as a LIST;
    zip them pairwise so each valid sha maps to a valid filename string.
    Uses the verified source pool file only; never reads cfg/api.yaml.
    Mirrors legacy server's POOL_BY_SHA structure; restored after use.
    """
    src = Path(source_root).resolve()
    data_root = (src / "data").resolve()
    pool_path = src / "data" / "creatives" / "cn" / POOL_FILENAME
    try:
        pool_resolved = pool_path.resolve(strict=True)
    except OSError:
        return {}
    if not pool_resolved.is_relative_to(data_root):  # symlink escapes not allowed
        return {}
    mapping = {}
    try:
        with open(pool_resolved, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue  # skip meta row / non-object records
                shas = rec.get("image_sha256")
                names = rec.get("image_ids")
                if isinstance(shas, list) and isinstance(names, list):
                    for sha, iid in zip(shas, names):
                        if isinstance(sha, str) and isinstance(iid, str) and sha not in mapping:
                            mapping[sha] = iid
    except OSError:
        pass
    return mapping


class _State:
    """Per-server state: roots, observer cache, lock."""

    def __init__(self, source_root, images_root, browse_root=None, asset_index=None, asset_roots=(), corpus_root=None, browse_index_root=None, calibration_root=None):
        self.source_root = os.path.abspath(source_root)
        self.images_root = os.path.abspath(images_root) if images_root else None
        self.lock = threading.Lock()
        self.observers = {}  # tag -> (observer, meta_mtime, event_mtime)
        self.browse_observers = {}  # retain one potentially large private trace
        self.pool_map = _load_pool_map(self.source_root)
        self.browse_root = Path(browse_root or CODE_ROOT / "runs" / "browse_out").resolve()
        self.browse_index_root = Path(browse_index_root).resolve() if browse_index_root else None
        self.asset_roots = [Path(r).resolve() for r in asset_roots]
        self.assets = {}
        self.corpus = None
        from flowmirror.analysis.calibration_observer import CalibrationObserver
        self.calibration = CalibrationObserver(calibration_root)
        if corpus_root:
            from flowmirror.analysis.corpus_observer import CorpusObserver
            self.corpus = CorpusObserver(corpus_root)
        indexes = asset_index if isinstance(asset_index, (list, tuple)) else ([asset_index] if asset_index else [])
        for index in indexes:
            with Path(index).open(encoding='utf-8') as stream:
                for line in stream:
                    row = json.loads(line)
                    # Index entries are only candidates. Resolve symlinks again at
                    # request time in asset_path; avoid 25k filesystem walks on boot.
                    path = Path(os.path.abspath(row['path']))
                    ref = row.get('asset_ref','')
                    if (re.fullmatch(r'asset_[A-Za-z0-9_-]{1,128}', ref)
                            and path.suffix.lower() in ('.jpg','.jpeg','.png','.webp','.gif')
                            and any(path.is_relative_to(root) for root in self.asset_roots)):
                        if ref in self.assets and self.assets[ref] != path:
                            raise ValueError('conflicting image reference across local indexes')
                        self.assets[ref] = path

    def asset_path(self, ref):
        path = self.assets.get(ref)
        if path is None:
            return None
        path = path.resolve()
        return path if path.is_file() and any(path.is_relative_to(root) for root in self.asset_roots) else None

    def browse_path(self, tag):
        if not RUN_TAG_RE.fullmatch(tag) or tag in (".", ".."):
            return None
        directory = (self.browse_root / tag).resolve()
        checkpoint = (directory / "browse_run.json").resolve()
        if directory.parent != self.browse_root or checkpoint.parent != directory:
            return None
        return directory if checkpoint.is_file() else None

    def list_browse_runs(self):
        if not self.browse_root.is_dir():
            return {"runs": []}
        return {"runs": [{"tag": p.name} for p in sorted(self.browse_root.iterdir())
                         if self.browse_path(p.name) is not None]}

    def browse_observer(self, directory):
        from flowmirror.analysis.browse_observer import BrowseObserver
        from flowmirror.platform.browse_store import checkpoint_version
        index_path = None
        index_version = None
        if self.browse_index_root is not None:
            candidate = (self.browse_index_root / (directory.name + '.json')).resolve()
            if candidate.parent == self.browse_index_root and candidate.is_file():
                index_path = candidate
                stat = candidate.stat()
                index_version = (stat.st_mtime_ns, stat.st_size)
        version = (checkpoint_version(directory), index_version)
        with self.lock:
            cached = self.browse_observers.get(directory)
            if cached is not None and cached[0] == version:
                return cached[1]
            observer = BrowseObserver(directory, index_path=index_path)
            self.browse_observers.clear()
            self.browse_observers[directory] = (version, observer)
            return observer

    def _run_paths(self, tag):
        if not RUN_TAG_RE.match(tag):
            return None
        runs_dir = (Path(self.source_root) / "runs" / "out").resolve()
        run_dir = Path(os.path.join(runs_dir, tag)).resolve()
        if runs_dir not in run_dir.parents:
            return None
        meta = (run_dir / "run_meta.json").resolve()
        events = (run_dir / "event_log.jsonl").resolve()
        if run_dir not in meta.parents or run_dir not in events.parents:
            return None  # external symlinked meta/events rejected before stat/read
        if not (meta.is_file() and events.is_file()):
            return None
        return str(run_dir), str(meta), str(events)

    def list_runs(self):
        runs_dir = os.path.join(self.source_root, "runs", "out")
        out = []
        try:
            names = sorted(os.listdir(runs_dir))
        except OSError:
            names = []
        for tag in names:
            if not RUN_TAG_RE.match(tag):
                continue
            if self._run_paths(tag):
                out.append({"tag": tag})
        return {"runs": out}

    def observer(self, tag):
        """Return a cached RunObserver for a completed run, or None."""
        if not RUN_TAG_RE.match(tag):
            return None
        paths = self._run_paths(tag)
        if not paths:
            return None
        _, meta, events = paths
        mt = (os.path.getmtime(meta), os.path.getmtime(events))
        with self.lock:
            entry = self.observers.get(tag)
            if entry is not None and (entry[1], entry[2]) == mt:
                return entry[0]
            from flowmirror.analysis.observer import RunObserver

            obs = RunObserver(self.source_root, tag)
            if len(self.observers) >= 4:
                self.observers.pop(next(iter(self.observers)))
            self.observers[tag] = (obs, mt[0], mt[1])
            return obs


def make_server(source_root, images_root=None, port=0, browse_root=None, asset_index=None, asset_roots=(), corpus_root=None, browse_index_root=None, calibration_root=None):
    state = _State(source_root, images_root, browse_root, asset_index, asset_roots, corpus_root, browse_index_root, calibration_root)

    class Handler(BaseHTTPRequestHandler):
        server_version = "CommunityViewer/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # disable path logging
            pass

        def _host_ok(self):
            host = self.headers.get("Host", "")
            return host in ("localhost:%d" % self.server.server_port,
                            "127.0.0.1:%d" % self.server.server_port)

        def _send(self, code, body, ctype):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj), "application/json;charset=utf-8")

        def do_HEAD(self):
            if not self._host_ok():
                self._send(403, b"", "text/plain")
            else:
                self._send(405, b"", "text/plain")

        def do_POST(self):
            self._send(403 if not self._host_ok() else 405, b"", "text/plain")

        do_PUT = do_DELETE = do_POST

        def do_GET(self):
            if not self._host_ok():
                self._send(403, "forbidden", "text/plain")
                return
            path = self.path.split("?", 1)[0]
            try:
                self._route(path)
            except Exception:
                self._json({"error": "bad request"}, 400)

        def _route(self, path):
            if path in ("/", "/community.html"):
                self._send_file(self._static_path("community.html"))
            elif path == "/community.js":
                self._send_file(self._static_path("community.js"))
            elif path == "/community_browse.js":
                self._send_file(self._static_path("community_browse.js"))
            elif path == '/community_account.js':
                self._send_file(self._static_path('community_account.js'))
            elif path == '/community_marketing.js':
                self._send_file(self._static_path('community_marketing.js'))
            elif path == '/community_behavior.js':
                self._send_file(self._static_path('community_behavior.js'))
            elif path in ('/corpus.html','/corpus.js'):
                self._send_file(self._static_path(path[1:]))
            elif path in ('/calibration.html','/calibration.js'):
                self._send_file(self._static_path(path[1:]))
            elif path in ('/market.html','/market.js'):
                self._send_file(self._static_path(path[1:]))
            elif path == '/api/community/calibration/reports':
                self._json(state.calibration.reports())
            elif path == '/api/community/calibration/report':
                tag = (self._query().get('report') or [''])[0]
                report = state.calibration.report(tag)
                self._json(report if report is not None else {'error':'report not found'},
                           200 if report is not None else 404)
            elif path in ('/api/community/corpus/summary','/api/community/corpus/posts'):
                if state.corpus is None:
                    self._json({'error':'no corpus configured'},404)
                elif path.endswith('/summary'):
                    self._json(state.corpus.inventory)
                else:
                    q = self._query()
                    self._json(state.corpus.page(**{k:(q.get(k) or [v])[0] for k,v in
                        [('channel','xhs'),('start',''),('end',''),('timing','dated'),('query','')]},
                        offset=int((q.get('offset') or ['0'])[0]),limit=int((q.get('limit') or ['24'])[0])))
            elif path == "/api/community/runs":
                self._json(state.list_runs())
            elif path == "/api/community/summary":
                self._api_summary()
            elif path == "/api/community/frame":
                self._api_frame()
            elif path == "/api/community/demo":
                from flowmirror.platform.browse_driver import demo_trace
                self._json(demo_trace())
            elif path == "/api/community/browse/runs":
                self._json(state.list_browse_runs())
            elif path in ("/api/community/browse/summary", "/api/community/browse/frame"):
                self._api_browse(path.endswith("/frame"))
            elif path == "/api/community/browse/macro":
                self._api_browse_macro()
            elif path.startswith("/api/community/image/"):
                self._api_image(path.rsplit("/", 1)[-1])
            elif path.startswith('/api/community/asset/'):
                self._send_file(state.asset_path(path.rsplit('/',1)[-1]))
            else:
                self._json({"error": "not found"}, 404)

        def _static_path(self, name):
            """Fixed known filenames only; resolve and require containment."""
            f = (WEB_ROOT / name).resolve()
            if WEB_ROOT not in f.parents:
                return None
            return f

        def _send_file(self, fpath):
            if fpath is None:
                self._json({"error": "not found"}, 404)
                return
            try:
                with open(fpath, "rb") as fh:
                    data = fh.read()
            except OSError:
                self._json({"error": "not found"}, 404)
                return
            ctype = mimetypes.guess_type(str(fpath))[0] or "application/octet-stream"
            self._send(200, data, ctype)

        def _query(self):
            from urllib.parse import urlparse, parse_qs
            return parse_qs(urlparse(self.path).query)

        def _api_summary(self):
            run = (self._query().get("run") or [""])[0]
            obs = state.observer(run)
            if obs is None:
                self._json({"error": "not found"}, 404)
                return
            self._json(obs.summary())

        def _api_browse(self, frame):
            from flowmirror.analysis.browse_observer import BrowseObserver
            query = self._query()
            directory = state.browse_path((query.get("run") or [""])[0])
            if directory is None:
                self._json({"error": "not found"}, 404)
                return
            observer = state.browse_observer(directory)
            if frame:
                phase = (query.get("phase") or [""])[0]
                if not phase.isdigit():
                    self._json({"error": "bad request"}, 400)
                    return
                self._json(observer.agent_trace((query.get("agent") or [""])[0], int(phase)))
            else:
                self._json(observer.summary())

        def _api_browse_macro(self):
            from flowmirror.analysis.browse_observer import BrowseObserver
            query = self._query()
            directory = state.browse_path((query.get("run") or [""])[0])
            if directory is None:
                self._json({"error": "not found"}, 404)
                return
            raw = (query.get("phase") or [""])[0]
            phase = None if raw == "" else int(raw) if raw.isascii() and raw.isdigit() else None
            if phase is None and raw != "":
                self._json({"error": "bad request"}, 400)
                return
            observer = state.browse_observer(directory)
            self._json(observer.macro(phase))

        def _api_frame(self):
            q = self._query()
            run = (q.get("run") or [""])[0]
            agent = (q.get("agent") or [""])[0]
            day = (q.get("day") or [""])[0]
            if not day.isdigit():
                self._json({"error": "bad request"}, 400)
                return
            obs = state.observer(run)
            if obs is None:
                self._json({"error": "not found"}, 404)
                return
            self._json(obs.agent_frame(agent, int(day)))

        def _api_image(self, sha):
            if not re.match(r"^[0-9a-f]{64}$", sha) or sha not in state.pool_map:
                self._json({"error": "not found"}, 404)
                return
            if state.images_root is None:
                self._json({"error": "not found"}, 404)
                return
            from web import server as legacy

            with _IMAGE_LOCK:  # module-global: shared across ALL server instances
                saved = getattr(legacy, "POOL_BY_SHA", None)
                legacy.POOL_BY_SHA = state.pool_map
                try:
                    ipath = legacy.image_path_for(Path(state.images_root), sha)
                finally:
                    legacy.POOL_BY_SHA = saved
            if ipath is None:
                self._json({"error": "not found"}, 404)
                return
            real = os.path.realpath(str(ipath))
            if not real.startswith(state.images_root + os.sep):
                self._json({"error": "not found"}, 404)
                return
            try:
                with open(real, "rb") as fh:
                    data = fh.read()
            except OSError:
                self._json({"error": "not found"}, 404)
                return
            ctype = mimetypes.guess_type(real)[0] or "application/octet-stream"
            self._send(200, data, ctype)

    return ThreadingHTTPServer((HOST, port), Handler)


def main():
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", default=default_root)
    ap.add_argument("--images-root", default=None)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--browse-root", default=None, help="independent browsing checkpoints, read-only")
    ap.add_argument('--asset-index', action='append', default=None, help='local image_index.jsonl; repeatable, never served as a file')
    ap.add_argument('--asset-root', action='append', default=[], help='explicit allowed local raster-image directory; repeatable')
    ap.add_argument('--corpus-root', default=None, help='local exported corpus; researcher-only, no database connection')
    ap.add_argument('--browse-index-root', default=None, help='optional derived indexes for completed journal runs; read-only')
    ap.add_argument('--calibration-root', default=None, help='optional precomputed research summaries; read-only, never trains via HTTP')
    args = ap.parse_args()

    root = os.path.abspath(args.source_root)  # read-only data repo; never on sys.path

    srv = make_server(root, args.images_root, args.port, args.browse_root, args.asset_index, args.asset_root, args.corpus_root, args.browse_index_root, args.calibration_root)
    print("http://%s:%d/community.html" % (HOST, srv.server_port), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
