"""Password hashing and session-cookie based login."""

import bcrypt
from fastapi import Request

from webapp.db import User, get_user_by_id

SESSION_KEY = "user_id"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def login_user(request: Request, user: User) -> None:
    request.session[SESSION_KEY] = user.id


def logout_user(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)


def get_current_user(request: Request) -> User | None:
    user_id = request.session.get(SESSION_KEY)
    if not user_id:
        return None
    return get_user_by_id(user_id)
