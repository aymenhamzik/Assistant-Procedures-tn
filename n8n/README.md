# n8n workflows (Gemini version)

- `chat_workflow.json`: the full assistant (31 nodes).
- `error_workflow.json`: sends the team a Telegram alert when the chat workflow fails.
- `build_workflow.py`: regenerates both files from `prompts/current/`. Run it after
  every prompt change, then re-import. Model names are constants at the top of the file.

## Credentials to create (Settings → Credentials)

| Credential type | Name it | Fields | Used by |
|---|---|---|---|
| Telegram API | Telegram bot | Access token from @BotFather | Trigger, Download voice/photo, all Send nodes |
| Google Gemini(PaLM) API | Gemini | Host: leave default; API key: your Gemini key | Router model, Answer model, Verifier model, Retry model |
| Header Auth | Gemini key header | Name: `x-goog-api-key` · Value: your Gemini key | Gemini: transcribe, Gemini: describe photo, Embed query |
| Header Auth | Qdrant key header | Name: `api-key` · Value: your Qdrant key | Search Qdrant |

The Gemini key goes in two credentials because the chat model nodes use n8n's
Gemini integration, while transcription, photo description, query embedding and
search are direct HTTP calls (so the bot embeds queries exactly like the Python
ingestion script embeds documents).

## Import

1. **Workflows → Import from file** → `chat_workflow.json`. Repeat for `error_workflow.json`.
2. Open the **Config** node (second node) and replace `REPLACE_WITH_YOUR_QDRANT_URL_ENDING_WITH_6333`
   with your Qdrant URL, e.g. `https://xxxx.us-west-2-0.aws.cloud.qdrant.io:6333`.
3. Open every node with a warning icon and select the credential from the table above.
   For the HTTP nodes: Authentication is already set to "Generic Credential Type → Header Auth";
   just pick the right credential.
4. In `error_workflow.json`, replace `REPLACE_WITH_TEAM_CHAT_ID` with your team group's
   chat ID. Then in the chat workflow: **Settings → Error workflow** → select it.
5. Make sure the knowledge base is loaded: `python src/ingest.py --strategy section`.
6. Save, activate, and message your bot.

## How the flow works

Telegram Trigger → Config → Detect input (text / voice / photo) → Route by type →
voice: download + Gemini transcription | photo: download + Gemini description | text →
User input → Router (structured output) → Answerable?
- no → clarifying question, "not covered yet", or welcome message
- yes → Embed query (Gemini) → Search Qdrant (top 4, filtered by category) →
  Build context → Answer agent → Verifier → Verified?
  - pass → Send answer
  - fail → Answer agent (retry) with the unsupported claims → Send corrected answer

## Free-tier limits

Each text message uses about 4 Gemini requests (router, embedding, answer, verifier),
5 for a voice note or photo, plus one more if the verifier fails. Limits are counted
per model, which is why the router and verifier use Flash-Lite. HTTP and LLM nodes
retry 3 times, 5 seconds apart, so a short rate-limit burst does not crash the run.

If you see `429` or `RESOURCE_EXHAUSTED` errors: wait, test less in bursts, check your
current limits in Google AI Studio, and keep a few recorded runs as a backup for the demo.
If a model name stops working, change the constants at the top of `build_workflow.py`,
re-run it and re-import (or edit the model nodes and the Config node by hand).

## Testing tips

- Use **Execute workflow**, send a real Telegram message, then click each node to
  inspect its input and output. Pin the trigger's output to replay the same message.
- Test each branch: a French text, a Darija voice note, a photo of a form, "salam",
  "how do I renew my driving licence", and a prompt-injection attempt.
- Log every bad output in `prompts/prompt_library.md`.

## If something breaks on import

The JSON targets recent n8n 1.x versions; node parameters occasionally change. If a node
shows an error or empty fields, set them by hand:

| Node | What to check |
|---|---|
| Route by type (Switch) | Mode "Expression", 3 outputs, expression returns 0 (voice), 1 (photo), 2 (text) |
| Download voice / photo | Telegram, resource File, operation Get, File ID `{{ $json.file_id }}`, Download on |
| Gemini: transcribe / describe photo / Embed query / Search Qdrant | HTTP Request, POST, body type JSON, "Using JSON" with the expression already filled in |
| Router model, etc. | Google Gemini Chat Model, model `models/gemini-2.5-flash-lite` (router, verifier) or `models/gemini-2.5-flash` (answer, retry) |
| Router / Verifier | "Require specific output format" on, Structured Output Parser attached |
| Search Qdrant returns an error | Check the Config URL (https, `:6333`, no trailing slash) and that `procedures_section` exists in the Qdrant dashboard |

Document any such fix in the report: it is a real integration issue you solved.
