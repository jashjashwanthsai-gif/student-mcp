"""Structured, append-only log of per-topic mastery updates.

This is the piece that makes the platform about LEARNING PATTERNS rather than
a voice/memory twin (contrast persona_mcp, which is built around recalling
*what was said* via semantic similarity search over chat chunks). A tutor
doesn't need to recall isolated facts about the student - it needs to reason
about trajectories: is this topic improving or stuck, and does the same kind
of mistake keep recurring across attempts? That's a trend over an ordered
log, not a nearest-neighbor text lookup, so it's kept as its own flat JSONL
file per student rather than folded into the Chroma store.

Kept file-based (students/<id>/progress_log.jsonl), not SQL, so the
single-tenant stdio server and the multi-tenant webapp share the exact same
mechanism with no extra dependency for the core library.
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from student_adaptive_mcp import config

STRUGGLE_LEVELS = {"struggling", "weak", "confused"}
MASTERED_LEVELS = {"mastered"}


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


def topic_trajectory(student_id: str, subject: str = "") -> dict[str, list[ProgressEntry]]:
    """Groups the log by topic (optionally filtered to a subject), preserving
    chronological order so callers see the mastery TREND, not just the latest
    state - e.g. "struggling -> practicing -> mastered" vs. just "mastered"."""
    subject_lower = subject.strip().lower()
    by_topic: dict[str, list[ProgressEntry]] = {}
    for entry in read_progress_log(student_id):
        if subject_lower and subject_lower not in entry.subject.lower() and subject_lower not in entry.topic.lower():
            continue
        by_topic.setdefault(entry.topic, []).append(entry)
    return by_topic


def summarize_knowledge_gaps(student_id: str, subject: str = "") -> list[str]:
    """Topics whose MOST RECENT logged mastery level indicates a struggle, with
    the full trend and any recurring note (often where the actual error
    pattern lives, e.g. "mixes up numerator/denominator" showing up 3 times
    in a row despite re-teaching) - the live, trend-aware counterpart to the
    baseline profile's static `weaknesses`."""
    summaries = []
    for topic, entries in topic_trajectory(student_id, subject).items():
        latest = entries[-1]
        if latest.mastery_level.lower() not in STRUGGLE_LEVELS:
            continue
        trend = " -> ".join(e.mastery_level for e in entries)
        notes = list(dict.fromkeys(e.notes for e in entries if e.notes))
        line = f"{topic}: {trend}"
        if notes:
            line += f" (recurring notes: {'; '.join(notes)})"
        summaries.append(line)
    return summaries


def summarize_trajectory(student_id: str, subject: str = "") -> list[str]:
    """Full trend per topic (struggling and mastered alike) - answers "how is
    this student actually progressing", not just "what are they bad at"."""
    lines = []
    for topic, entries in topic_trajectory(student_id, subject).items():
        trend = " -> ".join(e.mastery_level for e in entries)
        span = f"{len(entries)} update(s)"
        lines.append(f"{topic}: {trend} ({span})")
    return lines


def recurring_error_notes(student_id: str, min_occurrences: int = 2) -> list[str]:
    """Notes that have been logged more than once across any topic - a signal
    that a specific MISTAKE PATTERN (not just a weak topic) keeps recurring,
    which is exactly the kind of thing a flat weaknesses list can't surface."""
    counts: dict[str, int] = {}
    for entry in read_progress_log(student_id):
        if entry.notes:
            counts[entry.notes] = counts.get(entry.notes, 0) + 1
    return [note for note, count in counts.items() if count >= min_occurrences]
