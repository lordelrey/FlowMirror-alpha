"""Read-only access to explicitly configured, precomputed research summaries.

This module never builds datasets, trains models, reads databases or serves
arbitrary artifacts. Large prediction/interval rows stay in their local files.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

TAG = re.compile(r'^[A-Za-z0-9_.-]{1,128}$')


class CalibrationObserver:
    def __init__(self, root):
        self.root = Path(root).resolve() if root else None

    def path(self, tag):
        if self.root is None or not isinstance(tag, str) or not TAG.fullmatch(tag) or tag in ('.', '..'):
            return None
        directory = (self.root / tag).resolve()
        path = (directory / 'report.json').resolve()
        if directory.parent != self.root or path.parent != directory or not path.is_file():
            return None
        return path

    def report(self, tag):
        path = self.path(tag)
        if path is None:
            return None
        with path.open(encoding='utf-8') as stream:
            result = json.load(stream)
        if not isinstance(result, dict) or result.get('kind') != 'offline_temporal_calibration':
            return None
        return {key:result[key] for key in ('kind','label','created_at','limitations','nav','engagement','reference_sources')
                if key in result}

    def reports(self):
        result = []
        if self.root is not None and self.root.is_dir():
            for path in sorted(self.root.iterdir()):
                try:
                    report = self.report(path.name)
                except (OSError, ValueError):
                    continue
                if report is not None:
                    result.append({'tag':path.name})
        return {'reports':result}
