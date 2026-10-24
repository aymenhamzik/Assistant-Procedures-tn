# Tunisian administrative procedures assistant

Advanced Deep Learning final project (AI automation). A Telegram bot that answers
questions about Tunisian administrative procedures (national ID card, passport, and
company registration at the RNE) from text, voice notes (including Tunisian Darija)
or photos of documents. It uses RAG over a curated corpus and a multi-agent pipeline
orchestrated in n8n, running entirely on Google's free Gemini API tier.

## Architecture

Telegram message → input detection (text / voice → Gemini transcription / photo → Gemini vision)
→ **router agent** (category, language, query rewrite, clarification)
→ **retrieval** (gemini-embedding-001 query embedding + Qdrant search filtered by category)
→ **answer agent** (grounded checklist with citations, in the user's language)
→ **verifier agent** (checks every claim against the passages; one retry on failure)
→ Telegram reply.

The knowledge base is built offline by `src/ingest.py`. Ingestion is kept in Python
rather than n8n because the chunking comparison needs precise control over chunk
sizes, metadata and repeatable runs; the user-facing pipeline runs entirely in n8n.

## Corpus (8 procedures)

| Category | Procedures |
|---|---|
| identity | CIN first issue · CIN loss and renewal (organic law 2024-22) · passport first issue/renewal · passport change (damaged, full) · passport duplicate after loss |
| business | company name reservation · sole trader registration · SARL registration |

See `corpus/README.md` for collection rules and `corpus/conflicts.md` for
disagreements found between sources.

## Repository layout

```
corpus/              procedure documents (Markdown + metadata), conflicts log
src/chunking.py      chunking strategies: fixed-size and by section
src/ingest.py        chunk → embed → load into Qdrant
src/evaluate.py      retrieval evaluation (Hit@1, Hit@k, MRR) comparing strategies
eval/                24 test queries (French, English, Darija in both scripts) + results
prompts/current/     live prompts used by the workflow
prompts/             prompt library with versions and failure logs (deliverable)
n8n/                 importable workflows + generator script + import guide
```

## Quick start

1. **Python environment**
   ```
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   copy .env.example .env      # Windows (Mac/Linux: cp). Add your Gemini key and Qdrant URL
   ```
2. **Qdrant**: `docker run -p 6333:6333 qdrant/qdrant`, or a free Qdrant Cloud cluster.
3. **Load the knowledge base**
   ```
   python src/ingest.py --strategy section --dry-run   # inspect chunks, no API calls
   python src/ingest.py --strategy section              # used by the bot
   python src/ingest.py --strategy fixed --size 400 --overlap 50
   ```
4. **Evaluate retrieval**
   ```
   python src/evaluate.py --k 4
   python src/evaluate.py --k 4 --filter
   ```
   Results are saved in `eval/results_*.md`.
5. **Telegram bot**: message @BotFather, `/newbot`, keep the token.
6. **n8n**: n8n Cloud, or self-hosted with a public HTTPS URL (ngrok / Cloudflare Tunnel)
   so Telegram can reach the webhook. Import the workflows: see `n8n/README.md`.

## Models and cost

All models come from the Gemini API free tier (key from aistudio.google.com):
`gemini-2.5-flash` (answers, transcription, vision), `gemini-2.5-flash-lite` (router,
verifier) and `gemini-embedding-001` (3072-dimensional embeddings). Free-tier limits are
tight and not guaranteed, so the code backs off on rate-limit errors and the workflow
retries failed calls. Free-tier inputs may be used by Google to improve its models:
acceptable here because procedure information is public, and worth stating in the report.

## Notes on the chunking comparison

Procedure documents are short, so a 400-token fixed chunk often holds a whole
procedure and both strategies may score similarly. Also run smaller fixed sizes
(`--size 120 --overlap 20`, `--size 60 --overlap 10`) to show where fixed-size
splitting starts to cut document lists apart. That curve is a good figure for the report.



## Known limitations 

- Seed documents come mostly from idaraty.tn, an aggregator; verify against official sources.
- CIN requirements may change with the biometric card introduced by organic law 2024-22.
- Transcription of Tunisian Darija is less reliable than French or Modern Standard Arabic.
- Free-tier rate limits can interrupt testing or a live demo; keep recorded runs as backup.
- The verifier retries only once; a second failure still sends the corrected draft.

