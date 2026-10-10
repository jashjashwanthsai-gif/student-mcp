"""One-time LLM extraction of a student's learner profile from marks + notes + bio."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import anthropic

from student_adaptive_mcp.prompts import build_profile_prompt

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


def profile_to_dict(profile: LearnerProfile) -> dict:
    return asdict(profile)


def profile_from_dict(data: dict) -> LearnerProfile:
    return LearnerProfile(**data)


def save_profile(profile: LearnerProfile, path: Path) -> None:
    path.write_text(json.dumps(profile_to_dict(profile), indent=2), encoding="utf-8")


def load_profile(path: Path) -> LearnerProfile:
    data = json.loads(path.read_text(encoding="utf-8"))
    return profile_from_dict(data)
