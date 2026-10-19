"""Read-only classification of project files; never delete or open file contents."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import stat


TEMP_DIRS = {"tmp", ".tmp", ".codex-ppt-work"}
CACHE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def classify_file(relative: Path) -> str:
    """Protected data wins over any cache-like name below it."""
    parts = relative.parts
    if parts[0] in {"data", "output"} or relative.name.startswith(".env"):
        return "protected_data_or_deliverable"
    if parts[0] == "docs":
        return "documentation"
    if parts[0] in TEMP_DIRS:
        return "temporary_review_required"
    if any(part in CACHE_DIRS for part in parts) or relative.suffix == ".pyc":
        return "rebuildable_cache"
    if parts[:2] == ("frontend", "dist") or relative.suffix == ".tsbuildinfo":
        return "rebuildable_build_output"
    return "source_or_configuration"


def scan_project(root: Path) -> dict:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("项目路径必须是目录")
    groups = defaultdict(lambda: {"files": 0, "bytes": 0})
    review_locations = defaultdict(lambda: {"files": 0, "bytes": 0})
    skipped = []
    errors = []

    def on_error(error):
        errors.append({"path": os.path.relpath(error.filename, root), "reason": type(error).__name__})

    for current, directories, files in os.walk(root, onerror=on_error, followlinks=False):
        kept = []
        for name in directories:
            path = Path(current) / name
            try:
                # st_file_attributes also covers Windows junctions on Python 3.11.
                attributes = getattr(path.lstat(), "st_file_attributes", 0)
                linked = path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
            except OSError as error:
                on_error(error)
                skipped.append(path.relative_to(root).as_posix())
                continue
            if name in {".git", "node_modules", ".venv", "venv"} or linked:
                skipped.append(path.relative_to(root).as_posix())
            else:
                kept.append(name)
        directories[:] = kept
        for name in files:
            path = Path(current) / name
            relative = path.relative_to(root)
            if path.is_symlink():
                skipped.append(relative.as_posix())
                continue
            try:
                size = path.stat().st_size
            except OSError as error:
                on_error(error)
                continue
            category = classify_file(relative)
            groups[category]["files"] += 1
            groups[category]["bytes"] += size
            if category.startswith(("temporary_", "rebuildable_")):
                location = relative.parts[0] if category.startswith("temporary_") else relative.parent.as_posix()
                review_locations[location]["files"] += 1
                review_locations[location]["bytes"] += size
    return {
        "mode": "read_only",
        "content_read": False,
        "files_deleted": 0,
        "groups": dict(sorted(groups.items())),
        "review_locations": dict(sorted(review_locations.items())),
        "skipped": sorted(skipped),
        "errors": errors,
        "complete": not errors,
        "notice": "分类不代表可以直接删除；数据、备份、检查点与交付成果必须保留。跳过依赖、版本库和链接，体积不包含这些目录。",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(json.dumps(scan_project(args.root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
