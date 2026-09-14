"""Read-only store over one FlowMirror run directory (stdlib only)."""

import json
import os
import re

_TAG_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,119}$")


class ObserverStore:
    def __init__(self, source_root, tag):
        self.source_root = os.fspath(source_root)
        self.tag = tag
        self.meta = {}
        self.cfg = {}
        self.rows = []
        self.by_agent_day = {}
        self.posts = {}
        self.climate = {}
        self.days = {}
        self.agents = {}
        self.notes = {}
        self.nav = {}
        self.warnings = []
        self._load()

    # ---------- helpers ----------

    def _warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)

    def _safe(self, root, *parts):
        root = os.path.realpath(root)
        p = os.path.realpath(os.path.join(root, *parts))
        try:
            p = os.path.abspath(p)
            r = os.path.abspath(root)
            if p == r or p.startswith(r + os.sep):
                return p
        except Exception:
            pass
        return None

    def _read_text(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None

    def _read_json(self, path):
        txt = self._read_text(path)
        if txt is None:
            return None
        try:
            return json.loads(txt)
        except ValueError:
            return None

    # ---------- loading ----------

    def _load(self):
        if not isinstance(self.tag, str) or not _TAG_RE.match(self.tag):
            raise ValueError("invalid tag")
        if ".." in self.tag or os.path.isabs(self.tag):
            raise ValueError("invalid tag")
        out_dir = self._safe(self.source_root, "runs", "out")
        if out_dir is None:
            raise ValueError("invalid source root")
        run_dir = self._safe(out_dir, self.tag)
        if run_dir is None or not os.path.isdir(run_dir):
            raise ValueError("run not found")
        meta_path = self._safe(run_dir, "run_meta.json")
        if meta_path is None or not os.path.isfile(meta_path):
            raise ValueError("missing run meta")
        meta = self._read_json(meta_path)
        if not isinstance(meta, dict):
            raise ValueError("bad run meta")
        self.meta = meta
        cfg = meta.get("cfg")
        self.cfg = cfg if isinstance(cfg, dict) else {}

        log_path = self._safe(run_dir, "event_log.jsonl")
        if log_path is None or not os.path.isfile(log_path):
            raise ValueError("missing event log")
        self._load_log(log_path)
        self._load_inputs()

    def _load_log(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            raise ValueError("cannot read event log")
        n = len(lines)
        for idx, line in enumerate(lines):
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except ValueError:
                if idx == n - 1 and not line.endswith('\n'):
                    self._warn("truncated last log line skipped")
                    continue
                raise ValueError("malformed event log")
            if not isinstance(obj, dict):
                raise ValueError("malformed event log")
            self.rows.append(obj)
            t = obj.get("t")
            if isinstance(t, bool) or not isinstance(t, int):
                continue
            d = obj.get("d")
            if isinstance(d, str) and t not in self.days:
                self.days[t] = d
            ev = obj.get("ev")
            if "i" in obj:
                self.by_agent_day.setdefault((str(obj["i"]), t), []).append(obj)
            if ev == "post":
                pid = obj.get("p")
                if pid is not None:
                    self.posts[str(pid)] = obj
            elif ev == "clim":
                pid = obj.get("p")
                if pid is not None:
                    self.climate[(t, str(pid))] = obj

    def _data_path(self, rel):
        if not isinstance(rel, str) or not rel:
            return None
        base = os.path.realpath(os.path.join(self.source_root, "data"))
        p = os.path.realpath(os.path.join(self.source_root, rel))
        if os.path.commonpath([base, p]) != base:
            return None
        if not os.path.isfile(p):
            return None
        return p

    def _load_inputs(self):
        ap = self._data_path(self.cfg.get("agents_file"))
        if ap is None:
            self._warn("agents input unavailable")
        else:
            self._load_agents(ap)
        pp = self._data_path(self.cfg.get("content_pool"))
        if pp is None:
            self._warn("content pool unavailable")
        else:
            self._load_pool(pp)
        np = self._data_path(self.cfg.get("nav_cache"))
        if np is None:
            self._warn("nav input unavailable")
        else:
            self._load_nav(np)

    def _load_agents(self, path):
        data = self._read_json(path)
        if data is None:
            self._warn("agents input unreadable")
            return
        lst = data.get("agents") if isinstance(data, dict) else data
        if not isinstance(lst, list):
            self._warn("agents input malformed")
            return
        for a in lst:
            if isinstance(a, dict) and "id" in a:
                self.agents[str(a["id"])] = a

    def _load_pool(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        obj = json.loads(s)
                    except ValueError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("_meta") is True:
                        continue
                    nid = obj.get("note_id")
                    if nid is not None:
                        self.notes[str(nid)] = obj
        except OSError:
            self._warn("content pool unreadable")

    def _load_nav(self, path):
        data = self._read_json(path)
        if not isinstance(data, dict):
            self._warn("nav input unreadable")
            return
        data.pop("_meta", None)
        for code, series in data.items():
            if isinstance(series, dict):
                clean = {}
                for day, val in series.items():
                    if isinstance(val, (int, float)) and not isinstance(val, bool):
                        clean[str(day)] = float(val)
                self.nav[str(code)] = clean
