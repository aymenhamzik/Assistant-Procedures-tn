"""Shared helpers: config, corpus loading, Gemini embeddings, Qdrant client."""
import glob
import os
import time

import yaml
from dotenv import load_dotenv

load_dotenv()

EMBED_MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-001")
EMBED_DIM = int(os.getenv("EMBED_DIM", "3072"))
PREFIX = os.getenv("QDRANT_COLLECTION_PREFIX", "procedures")


def collection_name(strategy: str) -> str:
    return f"{PREFIX}_{strategy}"


def load_corpus(path: str) -> list[tuple[dict, str]]:
    """Load every Markdown file with a '---' metadata block under `path`."""
    docs = []
    for f in sorted(glob.glob(os.path.join(path, "**", "*.md"), recursive=True)):
        with open(f, encoding="utf-8") as fh:
            raw = fh.read()
        if not raw.startswith("---"):
            continue  # README, notes, etc.
        _, front, body = raw.split("---", 2)
        meta = yaml.safe_load(front) or {}
        # YAML turns dates into date objects; Qdrant payloads need plain JSON
        meta = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in meta.items()}
        meta["file"] = os.path.relpath(f, path)
        docs.append((meta, body.strip()))
    return docs


def get_gemini():
    from google import genai
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY is missing: check that your .env file exists and is filled in.")
    return genai.Client(api_key=key)


def embed(client, texts: list[str], task_type: str, batch: int = 20) -> list[list[float]]:
    """Embed texts with Gemini. task_type: RETRIEVAL_DOCUMENT for chunks, RETRIEVAL_QUERY for queries.

    The free tier has tight rate limits, so we send small batches and back off on 429 errors.
    """
    from google.genai import types

    vectors = []
    for i in range(0, len(texts), batch):
        part = texts[i:i + batch]
        for attempt in range(6):
            try:
                resp = client.models.embed_content(
                    model=EMBED_MODEL, contents=part,
                    config=types.EmbedContentConfig(task_type=task_type),
                )
                vectors.extend(e.values for e in resp.embeddings)
                break
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if ("429" in msg or "RESOURCE_EXHAUSTED" in msg) and attempt < 5:
                    wait = 20 * (attempt + 1)
                    print(f"  rate limited, waiting {wait}s...")
                    time.sleep(wait)
                else:
                    raise
        time.sleep(1)
    if vectors and len(vectors[0]) != EMBED_DIM:
        raise SystemExit(f"Embedding size is {len(vectors[0])} but EMBED_DIM={EMBED_DIM}: fix .env")
    return vectors


def get_qdrant():
    from qdrant_client import QdrantClient
    url = os.getenv("QDRANT_URL")
    if not url:
        raise SystemExit("QDRANT_URL is missing: check that your .env file exists and is filled in.")
    return QdrantClient(url=url, api_key=os.getenv("QDRANT_API_KEY") or None)
