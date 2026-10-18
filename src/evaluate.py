"""Retrieval evaluation: compare chunking strategies on the same test queries.

A query counts as a hit at rank r if the r-th retrieved chunk comes from the
expected procedure AND contains the expected answer text. This works for both
strategies, unlike checking section names (fixed chunks have no section).

Usage:
  python src/evaluate.py                       # both strategies, k=4
  python src/evaluate.py --k 3 --filter        # also filter by category
"""
import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import collection_name, embed, get_gemini, get_qdrant  # noqa: E402


def rank_of_hit(points, q):
    for i, p in enumerate(points, start=1):
        meta = p.payload.get("metadata", {})
        content = p.payload.get("content", "")
        if (meta.get("procedure_id") == q["expected_procedure"]
                and q["expected_contains"].lower() in content.lower()):
            return i
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", default="eval/test_queries.json")
    ap.add_argument("--strategies", nargs="+", default=["fixed", "section"])
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--filter", action="store_true", help="filter on metadata.category")
    args = ap.parse_args()

    from qdrant_client.models import FieldCondition, Filter, MatchValue

    with open(args.queries, encoding="utf-8") as f:
        queries = json.load(f)
    gm, qd = get_gemini(), get_qdrant()
    qvecs = embed(gm, [q["query"] for q in queries], task_type="RETRIEVAL_QUERY")

    summary, details = [], []
    for strategy in args.strategies:
        name = collection_name(strategy)
        ranks = []
        for q, vec in zip(queries, qvecs):
            flt = None
            if args.filter:
                flt = Filter(must=[FieldCondition(key="metadata.category",
                                                  match=MatchValue(value=q["category"]))])
            pts = qd.query_points(name, query=vec, limit=args.k,
                                  query_filter=flt, with_payload=True).points
            r = rank_of_hit(pts, q)
            ranks.append(r)
            top1 = pts[0].payload["metadata"]["procedure_id"] if pts else "-"
            details.append((strategy, q["id"], q["language"], q["query"], r, top1))
        n = len(ranks)
        summary.append((
            strategy,
            sum(1 for r in ranks if r == 1) / n,
            sum(1 for r in ranks if r) / n,
            sum(1 / r for r in ranks if r) / n,
        ))

    lines = [f"# Retrieval evaluation ({dt.datetime.now():%Y-%m-%d %H:%M})", "",
             f"{len(queries)} queries, k={args.k}, category filter={'on' if args.filter else 'off'}", "",
             "| Strategy | Hit@1 | Hit@k | MRR |", "|---|---|---|---|"]
    lines += [f"| {s} | {h1:.2f} | {hk:.2f} | {mrr:.2f} |" for s, h1, hk, mrr in summary]
    lines += ["", "## Per query", "", "| Strategy | ID | Lang | Query | Hit rank | Top-1 procedure |",
              "|---|---|---|---|---|---|"]
    lines += [f"| {s} | {i} | {l} | {qq} | {r or 'miss'} | {t} |" for s, i, l, qq, r, t in details]
    report = "\n".join(lines)
    print(report)

    out = f"eval/results_{dt.datetime.now():%Y%m%d_%H%M}.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
