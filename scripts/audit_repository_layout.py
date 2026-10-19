"""Read-only inventory and conservative archive candidates, not a delete tool.

File sizes include dependencies and temporary environments. Secret/data files
are never opened. Only project source/docs/configuration are read for references.
"""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
from zoneinfo import ZoneInfo


TEXT_ROOTS = {"backend", "frontend", "scripts", "tests", "experiments", "deploy", "docs"}
TEXT_EXTENSIONS = {".py", ".ts", ".vue", ".md", ".json", ".yaml", ".yml", ".toml", ".html", ".css"}
GENERATED = {"build", "dist", "node_modules", "__pycache__", ".git", ".tmp", ".codex", ".pytest_cache"}


def category(relative):
    parts = relative.parts
    if parts[0] == ".git":
        return "version_control_keep"
    if any("node_modules" == p for p in parts):
        return "installed_dependencies_keep_for_local_use"
    if parts[0] == "data" or relative.name.startswith(".env"):
        return "data_and_credentials_protected"
    if parts[0] == ".tmp":
        return "temporary_review_required"
    if "__pycache__" in parts or relative.suffix == ".pyc" or parts[0] == ".pytest_cache":
        return "regenerable_cache"
    if parts[0] == "build" or any(p.endswith(".egg-info") for p in parts):
        return "regenerable_packaging_output"
    if parts[:2] == ("frontend", "dist") or relative.suffix == ".tsbuildinfo":
        return "regenerable_frontend_output"
    if parts[0] == "docs":
        return "documentation_and_acceptance_keep_or_archive"
    if parts[:2] == ("experiments", "outputs"):
        return "experiment_results_keep_or_archive"
    return "source_and_configuration_keep"


def audit(root):
    root = root.resolve(strict=True)
    inventory, skipped, errors = [], [], []
    def error(exc):
        errors.append({"path": os.path.relpath(exc.filename, root), "type": type(exc).__name__})
    for current, directories, files in os.walk(root, followlinks=False, onerror=error):
        for name in list(directories):
            path = Path(current) / name
            try:
                info = path.lstat()
                if path.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                    directories.remove(name)
                    skipped.append(path.relative_to(root).as_posix())
            except OSError as exc:
                directories.remove(name)
                error(exc)
        for name in files:
            path = Path(current) / name
            try:
                if path.is_symlink():
                    skipped.append(path.relative_to(root).as_posix())
                    continue
                relative = path.relative_to(root)
                inventory.append({"path": relative.as_posix(), "bytes": path.stat().st_size,
                                  "category": category(relative)})
            except OSError as exc:
                error(exc)
    groups, top = defaultdict(lambda: {"files": 0, "bytes": 0}), defaultdict(lambda: {"files": 0, "bytes": 0})
    texts, imported = {}, defaultdict(set)
    for item in inventory:
        path = Path(item["path"])
        for mapping, key in ((groups, item["category"]), (top, path.parts[0])):
            mapping[key]["files"] += 1
            mapping[key]["bytes"] += item["bytes"]
        if ((path.parts[0] in TEXT_ROOTS or path.as_posix() in {"README.md", "pyproject.toml"}) and path.suffix in TEXT_EXTENSIONS and item["bytes"] < 2_000_000
                and not any(p in GENERATED for p in path.parts)
                and not (len(path.parts) > 1 and path.parts[1] in {"acceptance", "outputs"})
                and not path.name.startswith(".env")):
            try:
                body = (root / path).read_text(encoding="utf-8-sig")
                texts[path.as_posix()] = body
                if path.suffix == ".py":
                    for node in ast.walk(ast.parse(body)):
                        if isinstance(node, ast.ImportFrom) and node.module:
                            imported[node.module.replace(".", "/") + ".py"].add(path.as_posix())
                        elif isinstance(node, ast.Import):
                            for alias in node.names:
                                imported[alias.name.replace(".", "/") + ".py"].add(path.as_posix())
            except (OSError, UnicodeError, SyntaxError) as exc:
                errors.append({"path": path.as_posix(), "type": type(exc).__name__, "phase": "reference_scan"})
    history = []
    for item in inventory:
        path = Path(item["path"])
        if path.parts[0] != "scripts" or path.suffix != ".py":
            continue
        if not (path.name.startswith(("clean_", "remove_", "prune_")) or "yunnan" in path.stem):
            continue
        if path.name == "prune_attraction_index.py":
            continue  # Generic maintenance CLI, not a historical province batch.
        references = sorted(name for name, body in texts.items()
                            if name != path.as_posix() and (path.name in body or path.stem in body))
        history.append({"path": path.as_posix(), "imported_by": sorted(imported[path.as_posix()]),
                        "text_references": references,
                        "recommendation": "review_dependencies_before_archive" if references or imported[path.as_posix()]
                        else "historical_cli_archive_candidate_not_proven_unused"})
    # Exact duplicates: immutable source/docs/static assets only. Never hash
    # databases, credentials, backups, vectors, installed packages or test envs.
    sizes = defaultdict(list)
    for item in inventory:
        path = Path(item["path"])
        if item["category"] in {"source_and_configuration_keep", "documentation_and_acceptance_keep_or_archive"}:
            if path.parts[0] in {"backend", "frontend", "scripts", "docs", "deploy", "tests", "experiments"} and item["bytes"] > 0:
                sizes[item["bytes"]].append(item)
    hashes = defaultdict(list)
    for size, items in sizes.items():
        if len(items) < 2:
            continue
        for item in items:
            try:
                digest = hashlib.sha256((root / item["path"]).read_bytes()).hexdigest()
                hashes[(size, digest)].append(item["path"])
            except OSError as exc:
                errors.append({"path": item["path"], "type": type(exc).__name__, "phase": "duplicate_scan"})
    return {"created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), "read_only": True,
            "files_deleted": 0, "file_count": len(inventory), "logical_bytes": sum(i["bytes"] for i in inventory),
            "groups": dict(groups), "top_level": dict(sorted(top.items())), "skipped_links": sorted(skipped),
            "errors": errors, "complete": not errors, "inventory": inventory,
            "historical_script_review": history,
            "exact_duplicate_groups": [{"bytes_each": size, "paths": paths} for (size, _), paths in hashes.items() if len(paths) > 1],
            "limits": ["元数据统计包括依赖、临时环境和版本库；不读取这些文件的正文。",
                       "未发现静态引用不等于无用：独立CLI、动态导入和人工操作必须另行确认。",
                       "临时目录可能包含凭据和演练检查点，不能整目录自动删除。",
                       "符号链接/目录联接不跟随；无法访问的目录明确记录，不推断为空。"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("file_count", "logical_bytes", "groups", "top_level", "errors", "complete",
                                                 "historical_script_review", "exact_duplicate_groups", "skipped_links")}, ensure_ascii=False, indent=2))
