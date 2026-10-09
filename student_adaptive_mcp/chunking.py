"""Groups raw notes/syllabus/teacher-remarks text into chunks for embedding."""

from dataclasses import dataclass, field


@dataclass
class Chunk:
    text: str
    metadata: dict = field(default_factory=dict)


def chunk_notes(text: str, source_file: str, max_chars: int = 1500) -> list[Chunk]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[Chunk] = []
    buffer = ""

    def flush():
        nonlocal buffer
        if buffer:
            chunks.append(
                Chunk(
                    text=buffer,
                    metadata={"source_file": source_file, "chunk_index": len(chunks)},
                )
            )
        buffer = ""

    for para in paragraphs:
        if buffer and len(buffer) + len(para) + 2 > max_chars:
            flush()
        buffer = f"{buffer}\n\n{para}" if buffer else para

    flush()
    return chunks
