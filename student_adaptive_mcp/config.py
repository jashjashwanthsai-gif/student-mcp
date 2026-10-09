"""Filesystem layout for student data: students/<name>/{raw,chroma,profile.json,bio.txt}."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

BIO_PLACEHOLDER = """\
Write a short "about me" for this student here.

Age/grade, interests and hobbies (used for analogies - e.g. football, gaming,
art), any known learning differences (ADHD, dyslexia, etc.), and anything else
a tutor should know that might not show up in report cards or notes.
"""


def get_students_root() -> Path:
    root = os.environ.get("STUDENT_MCP_HOME")
    return Path(root) if root else REPO_ROOT / "students"


def get_student_dir(student_name: str) -> Path:
    return get_students_root() / student_name


def get_raw_dir(student_name: str) -> Path:
    return get_student_dir(student_name) / "raw"


def get_chroma_dir(student_name: str) -> Path:
    return get_student_dir(student_name) / "chroma"


def get_profile_path(student_name: str) -> Path:
    return get_student_dir(student_name) / "profile.json"


def get_bio_path(student_name: str) -> Path:
    return get_student_dir(student_name) / "bio.txt"


def ensure_student_scaffold(student_name: str) -> None:
    get_raw_dir(student_name).mkdir(parents=True, exist_ok=True)
    get_chroma_dir(student_name).mkdir(parents=True, exist_ok=True)
    bio_path = get_bio_path(student_name)
    if not bio_path.exists():
        bio_path.write_text(BIO_PLACEHOLDER, encoding="utf-8")
