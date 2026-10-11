"""Learner-profile extraction from marks + notes + bio.

Two ways to build a LearnerProfile:
- `build_learner_profile` - one Anthropic call, higher-fidelity (infers learning
  style, pacing, nuanced error patterns from free text). Needs ANTHROPIC_API_KEY.
- `build_learner_profile_heuristic` - plain stats over marks + keyword/regex
  rules over notes/bio. No model, no API key, costs nothing. Lower-fidelity
  (can't infer anything not covered by its keyword lists), but lets the
  platform run end-to-end - ingestion, profile, and `ask_tutor` via the host
  model - with zero LLM API key anywhere, the same tradeoff persona_mcp's
  style_profile.py makes for the same reason.

`student_adaptive_mcp/ingest.py` picks whichever is available at ingestion
time; nothing about the stored LearnerProfile shape differs between the two,
so a later re-ingestion with a key set can upgrade a heuristic profile to an
LLM one without any migration.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import anthropic

from student_adaptive_mcp.prompts import build_profile_prompt

if TYPE_CHECKING:
    from student_adaptive_mcp.report_parser import MarkRecord

MODEL = "claude-sonnet-5-5"


@dataclass
class LearnerProfile:
    grade_level: str
    overall_summary: str
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    error_patterns: list[str] = field(default_factory=list)
    learning_style: str = ""
    pacing_preference: str = ""
    interests_for_analogies: list[str] = field(default_factory=list)
    subject_notes: list[str] = field(default_factory=list)
    recommended_focus_topics: list[str] = field(default_factory=list)


def build_learner_profile(
    marks_summary: str,
    notes_sample: str,
    bio_text: str,
    student_name: str,
    client: anthropic.Anthropic,
) -> LearnerProfile:
    prompt = build_profile_prompt(student_name, marks_summary, notes_sample, bio_text)

    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = response.content[0].text.strip()
    # Model may wrap the JSON in a code fence despite instructions; strip it defensively.
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    data = json.loads(raw_text)
    return LearnerProfile(
        grade_level=data.get("grade_level", ""),
        overall_summary=data.get("overall_summary", ""),
        strengths=data.get("strengths", []),
        weaknesses=data.get("weaknesses", []),
        error_patterns=data.get("error_patterns", []),
        learning_style=data.get("learning_style", ""),
        pacing_preference=data.get("pacing_preference", ""),
        interests_for_analogies=data.get("interests_for_analogies", []),
        subject_notes=data.get("subject_notes", []),
        recommended_focus_topics=data.get("recommended_focus_topics", []),
    )


_GRADE_FIRST_PATTERN = re.compile(r"\b(grade|class|standard)\s*[:\-]?\s*(\d{1,2}|[a-z]+)\b", re.IGNORECASE)
_NUMBER_FIRST_PATTERN = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(grade|class|standard)\b", re.IGNORECASE)

_INTEREST_KEYWORDS = (
    "football", "soccer", "cricket", "basketball", "baseball", "tennis",
    "gaming", "video games", "minecraft", "roblox", "art", "drawing",
    "painting", "music", "singing", "guitar", "piano", "dance", "coding",
    "programming", "reading", "chess", "science", "space", "animals",
    "cars", "cooking", "anime", "lego",
)

# keyword -> generalized error-pattern phrase, same spirit as persona_mcp's
# style_profile.py heuristics: cheap signal, not semantic understanding.
_ERROR_PATTERN_KEYWORDS = (
    ("rush", "rushes through problems and makes careless errors under time pressure"),
    ("careless", "makes careless/silly mistakes rather than conceptual ones"),
    ("sign error", "drops or flips signs in multi-step calculations"),
    ("skip", "skips re-reading the question, missing details in word problems"),
    ("forget", "forgets intermediate steps in multi-step procedures"),
    ("confus", "confuses similarly-named or similarly-shaped concepts"),
    ("time pressure", "struggles to perform well under exam time pressure"),
    ("show work", "doesn't show work, making errors hard to trace back"),
    ("word problem", "has trouble translating word problems into equations"),
)

STRONG_THRESHOLD = 75.0
WEAK_THRESHOLD = 60.0


def _infer_grade_level(bio_text: str) -> str:
    match = _GRADE_FIRST_PATTERN.search(bio_text)
    if match:
        return f"{match.group(1).capitalize()} {match.group(2)}"
    match = _NUMBER_FIRST_PATTERN.search(bio_text)
    if match:
        return f"{match.group(2).capitalize()} {match.group(1)}"
    return "unknown, infer from interaction"


def _infer_interests(bio_text: str) -> list[str]:
    lower = bio_text.lower()
    return [kw for kw in _INTEREST_KEYWORDS if kw in lower]


def _infer_error_patterns(notes_texts: list[str]) -> list[str]:
    combined = " ".join(notes_texts).lower()
    return [phrase for kw, phrase in _ERROR_PATTERN_KEYWORDS if kw in combined]


def build_learner_profile_heuristic(
    records: list["MarkRecord"],
    notes_texts: list[str],
    bio_text: str,
    student_name: str,
) -> LearnerProfile:
    """No model, no API key - plain stats over marks plus keyword rules over
    notes/bio. See module docstring for the tradeoff vs build_learner_profile."""
    by_subject: dict[str, list["MarkRecord"]] = {}
    for r in records:
        by_subject.setdefault(r.subject, []).append(r)

    strengths: list[str] = []
    weaknesses: list[str] = []
    subject_notes: list[str] = []
    focus_candidates: list[tuple[float, str]] = []

    for subject, recs in by_subject.items():
        avg_pct = sum(r.percentage for r in recs) / len(recs)
        parts = [f"{(r.term + ': ') if r.term else ''}{r.score}/{r.max_score} ({r.percentage:.0f}%)" for r in recs]
        trend = ""
        if len(recs) > 1:
            if recs[-1].percentage > recs[0].percentage:
                trend = ", trending up"
            elif recs[-1].percentage < recs[0].percentage:
                trend = ", trending down"
            else:
                trend = ", holding steady"
        subject_notes.append(f"{subject}: " + "; ".join(parts) + trend)

        if avg_pct >= STRONG_THRESHOLD:
            strengths.append(f"{subject} (averaging {avg_pct:.0f}%)")
        elif avg_pct < WEAK_THRESHOLD:
            weaknesses.append(f"{subject} (averaging {avg_pct:.0f}%)")
            focus_candidates.append((avg_pct, subject))

    focus_candidates.sort(key=lambda pair: pair[0])
    recommended_focus_topics = [subject for _, subject in focus_candidates]

    if by_subject:
        total_pct = sum(r.percentage for recs in by_subject.values() for r in recs)
        total_count = sum(len(recs) for recs in by_subject.values())
        overall_avg = total_pct / total_count
        tier = "Strong" if overall_avg >= STRONG_THRESHOLD else "Developing" if overall_avg >= WEAK_THRESHOLD else "Needs focused support"
        overall_summary = (
            f"Averaging {overall_avg:.0f}% across {len(by_subject)} subject(s) from uploaded marks. "
            f"{tier} overall, with {len(weaknesses)} subject(s) averaging below {WEAK_THRESHOLD:.0f}%."
        )
    else:
        overall_summary = "No marks data provided yet - this profile is based on notes/bio only."

    return LearnerProfile(
        grade_level=_infer_grade_level(bio_text),
        overall_summary=overall_summary,
        strengths=strengths,
        weaknesses=weaknesses,
        error_patterns=_infer_error_patterns(notes_texts),
        learning_style="unknown, infer from interaction",
        pacing_preference="unknown, infer from interaction",
        interests_for_analogies=_infer_interests(bio_text),
        subject_notes=subject_notes,
        recommended_focus_topics=recommended_focus_topics,
    )


def profile_to_dict(profile: LearnerProfile) -> dict:
    return asdict(profile)


def profile_from_dict(data: dict) -> LearnerProfile:
    return LearnerProfile(**data)


def save_profile(profile: LearnerProfile, path: Path) -> None:
    path.write_text(json.dumps(profile_to_dict(profile), indent=2), encoding="utf-8")


def load_profile(path: Path) -> LearnerProfile:
    data = json.loads(path.read_text(encoding="utf-8"))
    return profile_from_dict(data)
