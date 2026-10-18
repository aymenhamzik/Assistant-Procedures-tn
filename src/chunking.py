"""Two chunking strategies, so we can compare them on the same test queries.

A) fixed_size_chunks: fixed token windows with overlap (baseline).
B) section_chunks: one chunk per '## ' section, with the procedure title
   prepended so every chunk knows which procedure it belongs to.
"""
import re
import unicodedata

import tiktoken

_enc = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_enc.encode(text))


def fixed_size_chunks(text: str, size: int = 400, overlap: int = 50) -> list[str]:
    """Split text into windows of `size` tokens, each overlapping the previous by `overlap`."""
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")
    tokens = _enc.encode(text)
    chunks, start = [], 0
    while start < len(tokens):
        chunks.append(_enc.decode(tokens[start:start + size]).strip())
        if start + size >= len(tokens):
            break
        start += size - overlap
    return [c for c in chunks if c]


def _slug(heading: str) -> str:
    text = unicodedata.normalize("NFKD", heading).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "section"


def section_chunks(title: str, body: str) -> list[tuple[str, str]]:
    """Return (section_slug, chunk_text) pairs, one per '## ' section."""
    parts = re.split(r"^## +", body, flags=re.M)
    chunks = []
    intro = parts[0].strip()
    if intro:
        chunks.append(("intro", f"{title}\n\n{intro}"))
    for part in parts[1:]:
        heading, _, content = part.partition("\n")
        content = content.strip()
        if content:
            chunks.append((_slug(heading), f"{title} — {heading.strip()}\n\n{content}"))
    return chunks
