"""CLI: parse raw student data, build a learner profile, and embed notes for recall.

Usage:
    uv run python -m student_adaptive_mcp.ingest --student "<name>"
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import anthropic

from student_adaptive_mcp import config, storage
from student_adaptive_mcp.chunking import chunk_notes
from student_adaptive_mcp.profile import LearnerProfile, build_learner_profile, save_profile
from student_adaptive_mcp.report_parser import MarkRecord, parse_marks_csv, summarize_marks

MAX_NOTES_SAMPLE_CHARS = 6000


@dataclass
class IngestionReport:
    marks_files_parsed: int = 0
    mark_records: int = 0
    subjects_found: int = 0
    notes_files_parsed: int = 0
    chunks_created: int = 0
    profile_built: bool = False
    profile: LearnerProfile | None = None
    sample_chunks: list[str] | None = None


def discover_source_files(raw_dir: Path) -> tuple[list[Path], list[Path]]:
    """Splits raw/ into (marks CSVs, notes files). Anything else is ignored."""
    mark_files: list[Path] = []
    note_files: list[Path] = []
    for f in sorted(raw_dir.glob("*")):
        if not f.is_file():
            continue
        if f.suffix.lower() == ".csv":
            mark_files.append(f)
        elif f.suffix.lower() in (".txt", ".md"):
            note_files.append(f)
    return mark_files, note_files


def _read_text(file_path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return file_path.read_text(encoding=encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return ""


def run_ingestion(
    student_name: str,
    display_name: str | None = None,
    raw_dir: Path | None = None,
    reset: bool = False,
    skip_profile: bool = False,
) -> IngestionReport:
    """student_name is the storage key (directory/collection name - a UUID for
    multi-tenant use). display_name is the human-readable name shown to the LLM
    in the profiling prompt; defaults to student_name for the single-tenant
    local CLI flow."""
    display_name = display_name or student_name
    config.ensure_student_scaffold(student_name)
    raw_dir = raw_dir or config.get_raw_dir(student_name)

    report = IngestionReport()
    collection = storage.reset_collection(student_name) if reset else storage.get_collection(student_name)

    mark_files, note_files = discover_source_files(raw_dir)
    if not mark_files and not note_files:
        print(f"No .csv/.txt/.md files found in {raw_dir}. Drop a marks CSV or notes there first.")
        return report

    all_records: list[MarkRecord] = []
    for mf in mark_files:
        records = parse_marks_csv(mf)
        all_records.extend(records)
        report.marks_files_parsed += 1
    report.mark_records = len(all_records)
    report.subjects_found = len({r.subject for r in all_records})

    notes_texts: list[str] = []
    for nf in note_files:
        text = _read_text(nf)
        if not text:
            continue
        notes_texts.append(text)
        report.notes_files_parsed += 1

        chunks = chunk_notes(text, source_file=nf.name)
        storage.add_chunks(collection, chunks)
        report.chunks_created += len(chunks)
        if report.sample_chunks is None and chunks:
            report.sample_chunks = [c.text for c in chunks[:2]]

    if not skip_profile:
        bio_path = config.get_bio_path(student_name)
        bio_text = bio_path.read_text(encoding="utf-8") if bio_path.exists() else ""
        marks_summary = summarize_marks(all_records)
        notes_sample = "\n\n---\n\n".join(notes_texts)[:MAX_NOTES_SAMPLE_CHARS]

        if all_records or notes_sample:
            client = anthropic.Anthropic()
            profile = build_learner_profile(marks_summary, notes_sample, bio_text, display_name, client)
            report.profile = profile
            report.profile_built = True

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest raw student data into a learner profile + recallable notes.")
    parser.add_argument("--student", required=True, help="Student name (used for data directory + collection)")
    parser.add_argument("--raw-dir", type=Path, default=None, help="Override raw data directory")
    parser.add_argument("--reset", action="store_true", help="Wipe existing vector store before ingesting")
    parser.add_argument("--skip-profile", action="store_true", help="Skip the Anthropic call for profile extraction")
    args = parser.parse_args(argv)

    report = run_ingestion(
        student_name=args.student,
        raw_dir=args.raw_dir,
        reset=args.reset,
        skip_profile=args.skip_profile,
    )

    print(f"Student: {args.student}")
    print(f"Marks files parsed: {report.marks_files_parsed}  (records: {report.mark_records}, subjects: {report.subjects_found})")
    print(f"Notes files parsed: {report.notes_files_parsed}  (chunks: {report.chunks_created})")
    print(f"Learner profile built: {report.profile_built}")
    if report.profile is not None:
        save_profile(report.profile, config.get_profile_path(args.student))
        print(f"Learner profile saved to {config.get_profile_path(args.student)}")
    if report.sample_chunks:
        print("\nSample notes chunk preview:")
        for i, text in enumerate(report.sample_chunks, 1):
            preview = text if len(text) <= 300 else text[:300] + "..."
            print(f"  [{i}] {preview}")

    bio_path = config.get_bio_path(args.student)
    print(f"\nBio file: {bio_path} (edit this by hand if you haven't already)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
