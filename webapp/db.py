"""Supabase-backed storage for user accounts, student metadata, and the
progress log.

Notes/history vector memory lives in Qdrant (see student_adaptive_mcp.storage)
- this module holds accounts, student identity/status, bio text, the learner
profile JSON, and the structured per-topic progress log. None of it lives in
local files or a local DB anymore, which is what lets this survive a Render
redeploy or free-tier spin-down.
"""

import os
import secrets
from dataclasses import dataclass

from supabase import Client, create_client

from student_adaptive_mcp.progress import ProgressEntry

_client: Client | None = None


def _get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    return _client


@dataclass
class User:
    id: str
    email: str
    password_hash: str
    created_at: str | None = None


@dataclass
class Student:
    id: str
    owner_id: str
    name: str
    mcp_token: str
    status: str = "created"
    status_message: str = ""
    bio_text: str = ""
    profile: dict | None = None
    created_at: str | None = None
    updated_at: str | None = None


def _user_from_row(row: dict) -> User:
    return User(id=row["id"], email=row["email"], password_hash=row["password_hash"], created_at=row.get("created_at"))


def _student_from_row(row: dict) -> Student:
    return Student(
        id=row["id"],
        owner_id=row["owner_id"],
        name=row["name"],
        mcp_token=row["mcp_token"],
        status=row.get("status") or "created",
        status_message=row.get("status_message") or "",
        bio_text=row.get("bio_text") or "",
        profile=row.get("profile"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _progress_entry_from_row(row: dict) -> ProgressEntry:
    return ProgressEntry(
        topic=row["topic"],
        subject=row.get("subject") or "",
        mastery_level=row["mastery_level"],
        notes=row.get("notes") or "",
        logged_at=row["created_at"],
    )


# --- Users -------------------------------------------------------------


def create_user(email: str, password_hash: str) -> User:
    result = _get_client().table("users").insert({"email": email, "password_hash": password_hash}).execute()
    return _user_from_row(result.data[0])


def get_user_by_email(email: str) -> User | None:
    result = _get_client().table("users").select("*").eq("email", email).limit(1).execute()
    return _user_from_row(result.data[0]) if result.data else None


def get_user_by_id(user_id: str) -> User | None:
    result = _get_client().table("users").select("*").eq("id", user_id).limit(1).execute()
    return _user_from_row(result.data[0]) if result.data else None


# --- Students ------------------------------------------------------------


def create_student(owner_id: str, name: str) -> Student:
    token = secrets.token_hex(32)
    result = (
        _get_client()
        .table("students")
        .insert({"owner_id": owner_id, "name": name, "mcp_token": token, "status": "created"})
        .execute()
    )
    return _student_from_row(result.data[0])


def get_student(student_id: str) -> Student | None:
    result = _get_client().table("students").select("*").eq("id", student_id).limit(1).execute()
    return _student_from_row(result.data[0]) if result.data else None


def get_student_by_token(token: str) -> Student | None:
    result = _get_client().table("students").select("*").eq("mcp_token", token).limit(1).execute()
    return _student_from_row(result.data[0]) if result.data else None


def get_students_for_owner(owner_id: str) -> list[Student]:
    result = (
        _get_client()
        .table("students")
        .select("*")
        .eq("owner_id", owner_id)
        .order("created_at", desc=True)
        .execute()
    )
    return [_student_from_row(row) for row in result.data]


def update_student_status(student_id: str, status: str, status_message: str = "") -> None:
    _get_client().table("students").update({"status": status, "status_message": status_message}).eq(
        "id", student_id
    ).execute()


def update_student_bio(student_id: str, bio_text: str) -> None:
    _get_client().table("students").update({"bio_text": bio_text}).eq("id", student_id).execute()


def update_student_profile(student_id: str, profile: dict) -> None:
    _get_client().table("students").update({"profile": profile}).eq("id", student_id).execute()


def delete_student(student_id: str) -> None:
    _get_client().table("students").delete().eq("id", student_id).execute()


# --- Progress log ----------------------------------------------------------


def log_progress(student_id: str, topic: str, mastery_level: str, notes: str = "", subject: str = "") -> ProgressEntry:
    result = (
        _get_client()
        .table("progress_log")
        .insert(
            {
                "student_id": student_id,
                "topic": topic.strip(),
                "subject": subject.strip(),
                "mastery_level": mastery_level.strip(),
                "notes": notes.strip(),
            }
        )
        .execute()
    )
    return _progress_entry_from_row(result.data[0])


def get_progress_entries(student_id: str) -> list[ProgressEntry]:
    result = (
        _get_client()
        .table("progress_log")
        .select("*")
        .eq("student_id", student_id)
        .order("created_at")
        .execute()
    )
    return [_progress_entry_from_row(row) for row in result.data]
