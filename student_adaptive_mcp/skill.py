"""Builds a static Claude Skill (a SKILL.md) from a student's profile + bio +
full notes text - the free-tier deliverable, as opposed to the live MCP
connector premium accounts get (see webapp/mcp_multi.py, prompts.py).

A Skill is just text Claude loads into its own context when relevant - no
server, no retrieval, no write-back. That's why the full notes text is
inlined directly here rather than left to a selective-retrieval step the way
ask_tutor/recall_notes do it for the live connector: there's no retrieval
mechanism in a Skill to do that job.
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from student_adaptive_mcp.profile import LearnerProfile

MAX_SKILL_NOTES_CHARS = 20000


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "student"


def build_skill_markdown(student_name: str, profile: "LearnerProfile", bio_text: str, notes_text: str) -> str:
    def _bulleted(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items) if items else "(none recorded)"

    notes_text = notes_text.strip()
    if len(notes_text) > MAX_SKILL_NOTES_CHARS:
        notes_text = notes_text[:MAX_SKILL_NOTES_CHARS] + "\n\n[...truncated - the uploaded notes were longer than fit here]"

    frontmatter = (
        "---\n"
        f"name: {slugify(student_name)}-learner-profile\n"
        f"description: Use this whenever tutoring, explaining a concept to, or helping {student_name} "
        "with homework or schoolwork - it has their grade level, strengths, specific knowledge gaps, "
        "and the recurring patterns in how they make mistakes.\n"
        "---\n"
    )

    body = f"""
# {student_name}'s learner profile

A one-time snapshot built from uploaded report cards/marks and notes as of
when it was generated - it does not update itself between sessions. Teach
{student_name} the way someone who has watched them learn for months would,
not with a generic explanation.

## About {student_name}
{bio_text.strip() or "(no bio provided)"}

## Academic snapshot
- Grade level: {profile.grade_level}
- Summary: {profile.overall_summary}
- Learning style: {profile.learning_style}
- Pacing preference: {profile.pacing_preference}
- Interests to use for analogies: {", ".join(profile.interests_for_analogies) or "(none recorded)"}

## Strengths
{_bulleted(profile.strengths)}

## Known knowledge gaps / weaknesses
{_bulleted(profile.weaknesses)}

## Recurring error patterns (how they tend to go wrong, not just where)
{_bulleted(profile.error_patterns)}

## Per-subject notes
{_bulleted(profile.subject_notes)}

## Recommended focus topics
{_bulleted(profile.recommended_focus_topics)}

## Uploaded notes, teacher remarks, and syllabus excerpts
{notes_text or "(none provided)"}

## Rules
- Teach at the pace and level implied above - don't default to a generic
  one-size-fits-all explanation.
- Watch for the error patterns above re-appearing in the conversation.
- Use the interests above as analogies where it helps.
- This is a static snapshot - it has no memory of this conversation or any
  other, and nothing said here gets saved anywhere. A tutor that remembers
  and grows across sessions needs the live MCP connector (premium), not
  this skill.
"""
    return frontmatter + body
