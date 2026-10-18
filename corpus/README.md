# Corpus

One Markdown file per procedure (and per language version), in `procedures/`.

## File format

Each file starts with a metadata block, then the content split into `## ` sections.
Use the same section names everywhere so section-based chunking stays consistent:
`## Résumé` (organisation, duration, cost), `## Pré-requis`, `## Pièces à fournir`, `## Étapes`.

```
---
procedure_id: rne-immatriculation-sarl      # unique, reused across languages
title: Première immatriculation d'une SARL au RNE
category: business                          # identity | civil_status | vehicles | business
language: fr                                # fr | ar
source_type: aggregator                     # official | aggregator
url: https://...
date_retrieved: 2026-10-18
---
```

## Collection rules

1. Prefer official sources (ministries, RNE, the national procedures platform if live).
   Use aggregators only when no official page exists, and mark them `source_type: aggregator`.
2. Always record `url` and `date_retrieved`. Procedures change; the date lets us
   prefer recent sources and explain staleness in the report.
3. When two sources disagree (fees, offices, documents), note it in `conflicts.md`
   and mention it in the report.
4. Rewrite content in clean Markdown; do not paste whole web pages with menus and ads.
5. Scanned PDFs and forms go in `corpus/pdfs/` and are converted to Markdown with OCR.

## Current files

Eight procedures, retrieved on 2026-10-18. Seven come from idaraty.tn (aggregator,
`source_type: aggregator`); `cin-perte-renouvellement` summarises organic law
n° 2024-22 (official). Verify the aggregator files against official sources before
the final demo, and add Arabic versions (`*.ar.md`) with the same `procedure_id`.
