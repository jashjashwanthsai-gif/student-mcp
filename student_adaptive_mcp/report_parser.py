"""Parses marks/report-card data from CSV files.

Known limitation: real report cards usually arrive as PDFs with inconsistent
layouts per school. The MVP expects marks transcribed/exported into a simple
CSV with flexible column names (e.g. "subject"/"course", "score"/"marks",
"max_score"/"total"/"out_of", "term"/"exam"/"semester"). Parsing arbitrary
report-card PDFs directly is not implemented yet - that's the natural next
step once this flow is validated.
"""

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MarkRecord:
    subject: str
    score: float
    max_score: float
    term: str
    source_file: str

    @property
    def percentage(self) -> float:
        return (self.score / self.max_score * 100) if self.max_score else 0.0


_SUBJECT_KEYS = ("subject", "course", "topic", "subject name")
_SCORE_KEYS = ("score", "marks", "marks_obtained", "marks obtained", "obtained")
_MAX_KEYS = ("max_score", "max score", "total", "out_of", "out of", "max_marks", "max marks")
_TERM_KEYS = ("term", "exam", "semester", "test", "exam_name")


def _find_key(fieldnames: list[str], candidates: tuple[str, ...]) -> str | None:
    lower = {f.lower().strip(): f for f in fieldnames}
    for c in candidates:
        if c in lower:
            return lower[c]
    return None


def _read_text(file_path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return file_path.read_text(encoding=encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"Could not decode {file_path} with any known encoding")


def parse_marks_csv(file_path: Path) -> list[MarkRecord]:
    """Parses a marks CSV. Returns an empty list (rather than raising) if the
    file doesn't have a recognizable subject/score column, so one malformed
    file doesn't abort the whole ingestion run."""
    records: list[MarkRecord] = []
    text = _read_text(file_path)
    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames:
        return records

    subject_key = _find_key(reader.fieldnames, _SUBJECT_KEYS)
    score_key = _find_key(reader.fieldnames, _SCORE_KEYS)
    max_key = _find_key(reader.fieldnames, _MAX_KEYS)
    term_key = _find_key(reader.fieldnames, _TERM_KEYS)
    if not subject_key or not score_key:
        return records

    for row in reader:
        subject = (row.get(subject_key) or "").strip()
        if not subject:
            continue
        try:
            score = float((row.get(score_key) or "").strip())
        except ValueError:
            continue
        try:
            max_score = float((row.get(max_key) or "").strip()) if max_key else 100.0
        except ValueError:
            max_score = 100.0
        term = (row.get(term_key) or "").strip() if term_key else ""
        records.append(
            MarkRecord(subject=subject, score=score, max_score=max_score, term=term, source_file=file_path.name)
        )
    return records


def summarize_marks(records: list[MarkRecord]) -> str:
    """Renders marks as a compact per-subject summary for the profiling prompt."""
    if not records:
        return "(no structured marks data provided)"

    by_subject: dict[str, list[MarkRecord]] = {}
    for r in records:
        by_subject.setdefault(r.subject, []).append(r)

    lines = []
    for subject, recs in by_subject.items():
        parts = [f"{(r.term + ': ') if r.term else ''}{r.score}/{r.max_score} ({r.percentage:.0f}%)" for r in recs]
        lines.append(f"- {subject}: " + "; ".join(parts))
    return "\n".join(lines)
