"""Restrict newly created credential/backup paths on both Windows and Unix."""
import csv
import os
from pathlib import Path
import subprocess


def protect_private(path: Path, *, recursive: bool = False):
    path = path.resolve(strict=True)
    if os.name != "nt":
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    identity = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                              capture_output=True, text=True, check=True)
    sid = next(csv.reader([identity.stdout.strip()]))[-1]
    rights = "(OI)(CI)F" if path.is_dir() else "F"
    args = ["icacls", str(path), "/inheritance:r", "/grant:r",
            f"*{sid}:{rights}", f"*S-1-5-18:{rights}", f"*S-1-5-32-544:{rights}"]
    if recursive:
        args.append("/T")
    completed = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if completed.returncode:
        raise RuntimeError("无法限制私有文件权限，请先检查目录权限")
