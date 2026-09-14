"""Explicit local image roots for request preparation. No downloads or models."""
from pathlib import Path
import re

from flowmirror.platform.warehouse import lines, RASTER


class IndexedImages:
    def __init__(self, index, roots):
        self.roots = [Path(p).resolve(strict=True) for p in roots]
        self.paths = {}
        for row in lines(index):
            ref = row.get('asset_ref', '')
            path = Path(row['path']).resolve()
            if not re.fullmatch(r'asset_[A-Za-z0-9_-]{1,128}', ref):
                raise ValueError('invalid image reference')
            if ref in self.paths:
                raise ValueError('duplicate image reference')
            if path.suffix.lower() in RASTER and any(path.is_relative_to(root) for root in self.roots):
                self.paths[ref] = path

    def __call__(self, ref):
        path = self.paths.get(ref)
        if path is None:
            return None
        path = path.resolve()  # re-check a replaced symlink before file access
        if not path.is_file() or not any(path.is_relative_to(root) for root in self.roots):
            return None
        from flowmirror.agents.prompt import image_data_url
        return image_data_url(str(path))
