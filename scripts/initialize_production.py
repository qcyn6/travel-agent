"""Explicit maintenance step; never runs implicitly in an API/worker."""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.auth import UserRepository, hash_password
from backend.config import get_settings
from backend.database import init_database, open_session
from backend.database.seed import seed_catalog


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset-admin-password", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    settings.validate_production_security()
    if not settings.bootstrap_admin_username or not settings.bootstrap_admin_password:
        raise SystemExit("必须先配置初始管理员账号与强密码")
    init_database()
    with open_session() as session:
        seed_catalog(session)
        repository = UserRepository(session)
        existing = repository.get_by_username(settings.bootstrap_admin_username)
        if existing is not None:
            if existing.role != "admin":
                raise SystemExit("同名账号不是管理员，请选择独立管理员用户名")
            if not args.reset_admin_password:
                raise SystemExit("管理员已存在；若需替换密码，请显式传入 --reset-admin-password")
            existing.password_hash = hash_password(settings.bootstrap_admin_password)
            existing.is_active = True
            session.commit()
        else:
            repository.create(username=settings.bootstrap_admin_username,
                              password=settings.bootstrap_admin_password, role="admin")
    print("数据库初始化完成；管理员密码未输出。升级已有数据库前必须先完成备份。")
