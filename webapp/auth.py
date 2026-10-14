"""Session-cookie based login - for owner accounts, and separately for a
student's own read-only dashboard login.

Password hashing itself lives in webapp/db.py (re-exported here for existing
callers) so both account types can share it without a circular import.
"""

from fastapi import Request

from webapp.db import Student, User, get_student, get_user_by_id, hash_password, verify_password

__all__ = [
    "hash_password",
    "verify_password",
    "login_user",
    "logout_user",
    "get_current_user",
    "login_student",
    "logout_student",
    "get_current_student",
]

SESSION_KEY = "user_id"
STUDENT_SESSION_KEY = "student_session_id"


def login_user(request: Request, user: User) -> None:
    request.session[SESSION_KEY] = user.id


def logout_user(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)


def get_current_user(request: Request) -> User | None:
    user_id = request.session.get(SESSION_KEY)
    if not user_id:
        return None
    return get_user_by_id(user_id)


def login_student(request: Request, student: Student) -> None:
    """Separate session key from SESSION_KEY above - a student session is
    scoped to read-only dashboard access and must never be mistaken for (or
    grant) owner permissions like upload/bio-edit/delete."""
    request.session[STUDENT_SESSION_KEY] = student.id


def logout_student(request: Request) -> None:
    request.session.pop(STUDENT_SESSION_KEY, None)


def get_current_student(request: Request) -> Student | None:
    student_id = request.session.get(STUDENT_SESSION_KEY)
    if not student_id:
        return None
    return get_student(student_id)
