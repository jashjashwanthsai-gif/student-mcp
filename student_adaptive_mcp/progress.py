"""Structured, append-only log of per-topic mastery updates.

This is the piece that makes the platform about LEARNING PATTERNS rather than
a voice/memory twin (contrast persona_mcp, which is built around recalling
*what was said* via semantic similarity search over chat chunks). A tutor
doesn't need to recall isolated facts about the student - it needs to reason
about trajectories: is this topic improving or stuck, and does the same kind
of mistake keep recurring across attempts? That's a trend over an ordered
log, not a nearest-neighbor text lookup, so it's kept separate from the
Qdrant-backed notes store (see storage.py) entirely.

The local single-tenant stdio server keeps this file-based
(students/<id>/progress_log.jsonl). The multi-tenant webapp instead logs
entries to a Supabase table (see webapp/db.py's log_progress/
get_progress_entries) so progress survives a redeploy - both flows feed the
same ProgressEntry dataclass into the `_from_entries` functions below, so the
trend/pattern computation itself isn't duplicated per backend.
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from student_adaptive_mcp import config

STRUGGLE_LEVELS = {"struggling", "weak", "confused"}
MASTERED_LEVELS = {"mastered"}

# Ordinal position for charting a mastery_level on a shared axis - mastery_level
# is free text (see update_learning_progress's docstring), so anything that
# isn't recognized as a struggle or mastery defaults to the middle tier rather
# than failing; "practicing" lands there naturally too.
MASTERY_TIERS = ("Struggling", "Practicing", "Mastered")


def mastery_ordinal(mastery_level: str) -> int:
    level = mastery_level.strip().lower()
    if level in STRUGGLE_LEVELS:
        return 0
    if level in MASTERED_LEVELS:
        return 2
    return 1


@dataclass
class ProgressEntry:
    topic: str
    subject: str
    mastery_level: str
    notes: str
    logged_at: str


def _log_path(student_id: str) -> Path:
    return config.get_student_dir(student_id) / "progress_log.jsonl"


def log_progress(student_id: str, topic: str, mastery_level: str, notes: str = "", subject: str = "") -> ProgressEntry:
    entry = ProgressEntry(
        topic=topic.strip(),
        subject=subject.strip(),
        mastery_level=mastery_level.strip(),
        notes=notes.strip(),
        logged_at=datetime.now(timezone.utc).isoformat(),
    )
    path = _log_path(student_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry)) + "\n")
    return entry


def read_progress_log(student_id: str) -> list[ProgressEntry]:
    path = _log_path(student_id)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(ProgressEntry(**json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue
    return entries


def group_by_topic(entries: list[ProgressEntry], subject: str = "") -> dict[str, list[ProgressEntry]]:
    """Groups a list of entries by topic (optionally filtered to a subject),
    preserving chronological order so callers see the mastery TREND, not just
    the latest state - e.g. "struggling -> practicing -> mastered" vs. just
    "mastered". Backend-agnostic: works the same whether entries came from the
    local JSONL log or a Supabase query."""
    subject_lower = subject.strip().lower()
    by_topic: dict[str, list[ProgressEntry]] = {}
    for entry in entries:
        if subject_lower and subject_lower not in entry.subject.lower() and subject_lower not in entry.topic.lower():
            continue
        by_topic.setdefault(entry.topic, []).append(entry)
    return by_topic


def topic_trajectory(student_id: str, subject: str = "") -> dict[str, list[ProgressEntry]]:
    """File-based wrapper around group_by_topic for the local single-tenant flow."""
    return group_by_topic(read_progress_log(student_id), subject)


def summarize_knowledge_gaps_from_entries(entries: list[ProgressEntry], subject: str = "") -> list[str]:
    """Topics whose MOST RECENT logged mastery level indicates a struggle, with
    the full trend and any recurring note (often where the actual error
    pattern lives, e.g. "mixes up numerator/denominator" showing up 3 times
    in a row despite re-teaching) - the live, trend-aware counterpart to the
    baseline profile's static `weaknesses`."""
    summaries = []
    for topic, topic_entries in group_by_topic(entries, subject).items():
        latest = topic_entries[-1]
        if latest.mastery_level.lower() not in STRUGGLE_LEVELS:
            continue
        trend = " -> ".join(e.mastery_level for e in topic_entries)
        notes = list(dict.fromkeys(e.notes for e in topic_entries if e.notes))
        line = f"{topic}: {trend}"
        if notes:
            line += f" (recurring notes: {'; '.join(notes)})"
        summaries.append(line)
    return summaries


def summarize_trajectory_from_entries(entries: list[ProgressEntry], subject: str = "") -> list[str]:
    """Full trend per topic (struggling and mastered alike) - answers "how is
    this student actually progressing", not just "what are they bad at"."""
    lines = []
    for topic, topic_entries in group_by_topic(entries, subject).items():
        trend = " -> ".join(e.mastery_level for e in topic_entries)
        span = f"{len(topic_entries)} update(s)"
        lines.append(f"{topic}: {trend} ({span})")
    return lines


def recurring_error_notes_from_entries(entries: list[ProgressEntry], min_occurrences: int = 2) -> list[str]:
    """Notes that have been logged more than once across any topic - a signal
    that a specific MISTAKE PATTERN (not just a weak topic) keeps recurring,
    which is exactly the kind of thing a flat weaknesses list can't surface."""
    counts: dict[str, int] = {}
    for entry in entries:
        if entry.notes:
            counts[entry.notes] = counts.get(entry.notes, 0) + 1
    return [note for note, count in counts.items() if count >= min_occurrences]


def summarize_knowledge_gaps(student_id: str, subject: str = "") -> list[str]:
    """File-based wrapper for the local single-tenant flow - see
    summarize_knowledge_gaps_from_entries for the backend-agnostic logic."""
    return summarize_knowledge_gaps_from_entries(read_progress_log(student_id), subject)


def summarize_trajectory(student_id: str, subject: str = "") -> list[str]:
    """File-based wrapper for the local single-tenant flow - see
    summarize_trajectory_from_entries for the backend-agnostic logic."""
    return summarize_trajectory_from_entries(read_progress_log(student_id), subject)


def recurring_error_notes(student_id: str, min_occurrences: int = 2) -> list[str]:
    """File-based wrapper for the local single-tenant flow - see
    recurring_error_notes_from_entries for the backend-agnostic logic."""
    return recurring_error_notes_from_entries(read_progress_log(student_id), min_occurrences)
