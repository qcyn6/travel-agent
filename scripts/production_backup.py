"""Full MySQL + guides + consistent SQLite backup; isolated restore rehearsal.

Never overwrites a database or backup directory. Passwords stay out of argv
and console output. Backups contain private user data: keep them access-limited.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.engine import make_url

from backend.config import get_settings
from backend.database import get_engine
from scripts.private_files import protect_private


def mysql_binary(name):
    found = shutil.which(name)
    if found:
        return found
    for path in Path("C:/Program Files/MySQL").glob(f"MySQL Server */bin/{name}.exe"):
        return str(path)
    raise RuntimeError(f"未安装 {name}，请先安装 MySQL 客户端")


def mysql_command(binary, url, database):
    # The environment belongs only to this short-lived child process.
    env = {**os.environ, "MYSQL_PWD": url.password or ""}
    args = [mysql_binary(binary), "--host", url.host or "127.0.0.1", "--port", str(url.port or 3306),
            "--user", url.username or "", "--default-character-set=utf8mb4"]
    return args + [database], env


def fingerprint(engine):
    result = {}
    with engine.connect() as connection:
        for name in sorted(inspect(engine).get_table_names()):
            table = Table(name, MetaData(), autoload_with=engine)
            columns = list(table.primary_key.columns) or list(table.columns)
            digest, count = hashlib.sha256(), 0
            for row in connection.execution_options(stream_results=True).execute(select(table).order_by(*columns)).mappings():
                digest.update(json.dumps(dict(row), sort_keys=True, ensure_ascii=False, default=str,
                                         separators=(",", ":")).encode())
                digest.update(b"\n")
                count += 1
            result[name] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup(destination):
    settings = get_settings()
    url = make_url(settings.database_url)
    if not url.database or not re.fullmatch(r"[A-Za-z0-9_]+", url.database):
        raise RuntimeError("数据库名称不符合备份安全规则")
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    protect_private(destination)
    before = fingerprint(get_engine())
    args, env = mysql_command("mysqldump", url, url.database)
    args[-1:-1] = ["--single-transaction", "--quick", "--hex-blob", "--no-tablespaces",
                    "--routines", "--events", "--triggers", "--set-gtid-purged=OFF"]
    with (destination / "database.sql").open("xb") as output:
        completed = subprocess.run(args, env=env, stdout=output, stderr=subprocess.PIPE, timeout=300)
    if completed.returncode:
        raise RuntimeError("MySQL 导出失败；未写入有效备份清单，请检查客户端和数据库权限")
    if fingerprint(get_engine()) != before:
        raise RuntimeError("备份期间业务数据发生变化，请在维护窗口重试；本次目录不标记为可恢复")
    guides = Path(settings.guide_knowledge_path)
    if guides.exists():
        for path in guides.rglob("*"):
            if path.is_symlink():
                raise RuntimeError("指南目录包含符号链接，请先人工检查")
            if path.is_file():
                target = destination / "guides" / path.relative_to(guides)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    checkpoint = Path(settings.planning_checkpoint_path) if settings.planning_checkpoint_path else None
    if checkpoint and checkpoint.is_file():
        with sqlite3.connect(checkpoint.resolve().as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(destination / "planning_checkpoints.sqlite3") as target:
                source.backup(target)
    files = {str(p.relative_to(destination)).replace("\\", "/"): file_hash(p)
             for p in destination.rglob("*") if p.is_file()}
    manifest = {"format": 1, "created_at": datetime.now().isoformat(), "source_database": url.database,
                "tables": before, "files": files,
                "vector_recovery": "Chroma is rebuildable from MySQL attractions and guide Markdown; not copied live."}
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"backup_directory": str(destination), "tables": len(before),
            "rows": sum(item["rows"] for item in before.values()), "files": len(files)}


def verify_manifest(directory):
    directory = directory.resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1:
        raise RuntimeError("不支持的备份格式")
    for name, expected in manifest["files"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or path.is_symlink() or not path.is_file() or file_hash(path) != expected:
            raise RuntimeError("备份文件校验失败，停止恢复")
    if "database.sql" not in manifest["files"]:
        raise RuntimeError("缺少数据库备份")
    return manifest


def restore_rehearsal(directory, target_database):
    if not re.fullmatch(r"travel_restore_[A-Za-z0-9_]{8,48}", target_database):
        raise RuntimeError("只允许恢复到新建的 travel_restore_ 前缀演练数据库")
    manifest = verify_manifest(directory)
    url = make_url(get_settings().database_url)
    if target_database in {url.database, manifest["source_database"]}:
        raise RuntimeError("禁止覆盖源数据库")
    # Our dump intentionally omits --databases. Refuse one that can switch DB.
    with (directory / "database.sql").open(encoding="utf-8") as source:
        if any(re.match(r"^\s*(USE\s|CREATE\s+DATABASE\s)", line, re.I) for line in source):
            raise RuntimeError("备份包含数据库切换命令，拒绝自动演练")
    admin = create_engine(url.set(database=None))
    try:
        with admin.begin() as connection:
            # No IF NOT EXISTS: never import into an existing database.
            connection.execute(text(f"CREATE DATABASE `{target_database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
        args, env = mysql_command("mysql", url, target_database)
        with (directory / "database.sql").open("rb") as source:
            completed = subprocess.run(args, env=env, stdin=source, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.PIPE, timeout=300)
        if completed.returncode:
            raise RuntimeError("隔离恢复失败；演练库保留供排查，源库未修改")
        restored = create_engine(url.set(database=target_database))
        try:
            equal = fingerprint(restored) == manifest["tables"]
        finally:
            restored.dispose()
        result = {"target_database": target_database, "all_table_rows_and_hashes_match": equal,
                  "source_database_unchanged": fingerprint(get_engine()) == manifest["tables"],
                  "tables": len(manifest["tables"]), "database_retained_for_review": True}
        if not equal:
            raise RuntimeError("恢复后的数据摘要不一致，未通过演练")
        return result
    finally:
        admin.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "restore-rehearsal"])
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--target-database")
    args = parser.parse_args()
    directory = args.directory or ROOT / "data" / "backups" / f"full-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
    try:
        if args.action == "backup":
            result = backup(directory)
        else:
            if not args.directory or not args.target_database:
                parser.error("恢复演练必须明确指定 --directory 和 --target-database")
            result = restore_rehearsal(directory.resolve(), args.target_database)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        # Driver/CLI exception messages may contain connection credentials.
        print(json.dumps({"error_type": type(exc).__name__, "message": str(exc) if isinstance(exc, RuntimeError) else "操作失败，源数据库未被覆盖"}, ensure_ascii=False))
        raise SystemExit(1)
