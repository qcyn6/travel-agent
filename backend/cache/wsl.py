import os
import socket
import subprocess
import time
from urllib.parse import urlparse


def ensure_wsl_service_available(
    distro: str | None,
    redis_url: str,
    *,
    startup_timeout_seconds: float = 5.0,
) -> bool:
    """Keep a WSL distro alive so Windows clients can reach its Redis service."""
    if os.name != "nt" or not distro:
        return False
    hidden = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    check = subprocess.run(
        ["wsl.exe", "-d", distro, "--exec", "pgrep", "-f", "^sleep infinity$"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=hidden,
        check=False,
    )
    if check.returncode != 0:
        subprocess.Popen(
            ["wsl.exe", "-d", distro, "--exec", "sleep", "infinity"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=hidden,
        )
    parsed = urlparse(redis_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 6379
    deadline = time.monotonic() + startup_timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            time.sleep(0.1)
    return False
