"""Create a new private deployment configuration; never overwrites credentials."""
import argparse
import os
from pathlib import Path
import re
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.private_files import protect_private


def prepare(destination: Path, domain: str):
    if not re.fullmatch(r"(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", domain) or "." not in domain:
        raise ValueError("请填写不含协议或路径的实际域名")
    template = Path(__file__).resolve().parents[1] / "deploy" / "production.env.example"
    replacements = {key: secrets.token_hex(32) for key in (
        "MYSQL_ROOT_PASSWORD", "MYSQL_APP_PASSWORD", "REDIS_PASSWORD", "AUTH_SECRET", "BOOTSTRAP_ADMIN_PASSWORD")}
    replacements["DOMAIN"] = domain.lower()
    lines = []
    for line in template.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0]
        lines.append(f"{key}={replacements[key]}" if key in replacements else line)
    # O_EXCL and mode 0600 prevent accidental overwrite / world-readable creation.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        protect_private(destination)
    except Exception:
        os.close(descriptor)
        raise
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
        output.write("\n".join(lines) + "\n")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", required=True)
    parser.add_argument("--output", type=Path, default=Path("deploy/.env.production"))
    args = parser.parse_args()
    path = prepare(args.output, args.domain)
    print(f"已生成独立随机密钥：{path}。密钥未输出；请在服务器上填入外部服务配置。")
