"""Chunk the corpus, embed it, and load it into Qdrant.

Usage:
  python src/ingest.py --strategy section
  python src/ingest.py --strategy fixed --size 400 --overlap 50
  python src/ingest.py --strategy section --dry-run   # no API calls, just show chunks

Payload format is {"content": ..., "metadata": {...}} so the n8n Qdrant node
(LangChain-based) can read the chunks directly.
"""
import argparse
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from chunking import count_tokens, fixed_size_chunks, section_chunks  # noqa: E402
from common import (EMBED_DIM, collection_name, embed, get_gemini,  # noqa: E402
                    get_qdrant, load_corpus)


def build_chunks(docs, strategy, size, overlap):
    chunks = []
    for meta, body in docs:
        title = meta.get("title", meta.get("procedure_id", "untitled"))
        if strategy == "fixed":
            pieces = [(None, t) for t in fixed_size_chunks(f"{title}\n\n{body}", size, overlap)]
        else:
            pieces = section_chunks(title, body)
        for i, (section, text) in enumerate(pieces):
            chunks.append({
                "content": text,
                "metadata": {**meta, "section": section, "chunk_index": i, "strategy": strategy},
            })
    return chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", choices=["fixed", "section"], required=True)
    ap.add_argument("--size", type=int, default=400, help="fixed strategy: tokens per chunk")
    ap.add_argument("--overlap", type=int, default=50, help="fixed strategy: overlap in tokens")
    ap.add_argument("--corpus", default="corpus")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    docs = load_corpus(args.corpus)
    chunks = build_chunks(docs, args.strategy, args.size, args.overlap)
    sizes = [count_tokens(c["content"]) for c in chunks]
    print(f"{len(docs)} documents -> {len(chunks)} chunks "
          f"(tokens: min {min(sizes)}, avg {sum(sizes)//len(sizes)}, max {max(sizes)})")

    if args.dry_run:
        for c in chunks:
            m = c["metadata"]
            print(f"\n--- {m['procedure_id']} | section={m['section']} | {count_tokens(c['content'])} tok")
            print(c["content"][:300])
        return

    from qdrant_client.models import Distance, PointStruct, VectorParams

    vectors = embed(get_gemini(), [c["content"] for c in chunks], task_type="RETRIEVAL_DOCUMENT")
    qd = get_qdrant()
    name = collection_name(args.strategy)
    if qd.collection_exists(name):
        qd.delete_collection(name)
    qd.create_collection(name, vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE))
    qd.create_payload_index(name, field_name="metadata.category", field_schema="keyword")
    qd.upsert(name, points=[
        PointStruct(id=str(uuid.uuid4()), vector=v, payload=c)
        for c, v in zip(chunks, vectors)
    ])
    print(f"Loaded {len(chunks)} chunks into Qdrant collection '{name}'")


if __name__ == "__main__":
    main()
