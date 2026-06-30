"""CLI создания пользователя (bootstrap первого администратора).

Пример:
    python -m scripts.create_user --username admin
    python -m scripts.create_user --username admin --password secret
"""

from __future__ import annotations

import argparse
import getpass
import sys

from app.auth import service
from app.database import SessionLocal


def main() -> int:
    parser = argparse.ArgumentParser(description="Создать пользователя")
    parser.add_argument("--username", required=True, help="Логин пользователя")
    parser.add_argument("--password", help="Пароль (если не задан — спросит интерактивно)")
    args = parser.parse_args()

    password = args.password
    if not password:
        password = getpass.getpass("Пароль: ")
        confirm = getpass.getpass("Повторите пароль: ")
        if password != confirm:
            print("Пароли не совпадают.", file=sys.stderr)
            return 1
    if not password:
        print("Пароль не может быть пустым.", file=sys.stderr)
        return 1

    db = SessionLocal()
    try:
        if service.get_user_by_username(db, args.username):
            print(f"Пользователь «{args.username}» уже существует.", file=sys.stderr)
            return 1
        user = service.create_user(db, args.username, password)
        print(f"Создан пользователь «{user.username}» (id={user.id}).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
