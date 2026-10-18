"""Generate the importable n8n workflows (Gemini version) from the prompt files.

  python n8n/build_workflow.py

Writes n8n/chat_workflow.json and n8n/error_workflow.json. Re-run it after
editing anything in prompts/current/, then re-import the workflow in n8n.

Design notes:
- LLM chain prompts contain no curly braces: n8n's chain nodes build LangChain
  prompt templates, where curly braces are read as template variables.
- Query embedding and the Qdrant search are plain HTTP calls, so the bot embeds
  queries exactly like src/ingest.py embeds documents (same model, same size,
  RETRIEVAL_QUERY task type) and we control the category filter precisely.
- Router and verifier use the Flash-Lite model, the rest uses Flash: free-tier
  limits are counted per model, so splitting spreads the load.
"""
import json
import os
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Change these if Google renames or retires models (check aistudio.google.com)
MAIN_MODEL = "gemini-2.5-flash"         # answer, retry, transcription, photo
LIGHT_MODEL = "gemini-2.5-flash-lite"   # router, verifier
EMBED_MODEL = "gemini-embedding-001"    # must match EMBED_MODEL in .env
COLLECTION = "procedures_section"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models/"

RETRY = {"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 5000}


def prompt(name, braces_ok=False):
    with open(os.path.join(ROOT, "prompts", "current", f"{name}.txt"), encoding="utf-8") as f:
        text = f.read().strip()
    if not braces_ok:
        assert "{" not in text and "}" not in text, f"{name}.txt must not contain curly braces"
    return text


def node(name, type_, version, pos, params, **extra):
    return {"id": str(uuid.uuid4()), "name": name, "type": type_, "typeVersion": version,
            "position": pos, "parameters": params, **extra}


def chat_model(name, pos, model, temperature):
    return node(name, "@n8n/n8n-nodes-langchain.lmChatGoogleGemini", 1, pos, {
        "modelName": f"models/{model}",
        "options": {"temperature": temperature},
    })


def llm_chain(name, pos, text, system, parser=False):
    return node(name, "@n8n/n8n-nodes-langchain.chainLlm", 1.5, pos, {
        "promptType": "define",
        "text": text,
        "hasOutputParser": parser,
        "messages": {"messageValues": [{"message": system}]},
    }, **RETRY)


def parser(name, pos, schema):
    return node(name, "@n8n/n8n-nodes-langchain.outputParserStructured", 1.2, pos, {
        "schemaType": "manual", "inputSchema": json.dumps(schema, indent=2),
    })


def telegram_send(name, pos, chat_id_expr, text_expr):
    return node(name, "n8n-nodes-base.telegram", 1.2, pos, {
        "chatId": chat_id_expr, "text": text_expr,
        "additionalFields": {"appendAttribution": False},
    })


def telegram_get_file(name, pos):
    return node(name, "n8n-nodes-base.telegram", 1.2, pos, {
        "resource": "file", "fileId": "={{ $json.file_id }}", "download": True,
    })


def http_post(name, pos, url_expr, body_expr):
    """POST JSON with a Header Auth credential (Gemini: x-goog-api-key, Qdrant: api-key)."""
    return node(name, "n8n-nodes-base.httpRequest", 4.2, pos, {
        "method": "POST",
        "url": url_expr,
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": body_expr,
        "options": {},
    }, **RETRY)


def set_node(name, pos, fields):
    return node(name, "n8n-nodes-base.set", 3.4, pos, {
        "assignments": {"assignments": [
            {"id": str(uuid.uuid4()), "name": k, "value": v, "type": "string"} for k, v in fields
        ]},
        "options": {},
    })


def if_true(name, pos, expr):
    """IF node that passes when a JavaScript boolean expression is true."""
    return node(name, "n8n-nodes-base.if", 2.2, pos, {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
            "conditions": [{
                "id": str(uuid.uuid4()), "leftValue": expr, "rightValue": "",
                "operator": {"type": "boolean", "operation": "true", "singleValue": True},
            }],
            "combinator": "and",
        },
        "options": {},
    })


CFG = "$('Config').first().json"

# ---------------------------------------------------------------- code nodes

DETECT_INPUT = r"""// Normalise the Telegram update into one shape for the rest of the workflow
const msg = $('Telegram Trigger').first().json.message || {};
let input_type = 'text';
let file_id = null;
if (msg.voice) { input_type = 'voice'; file_id = msg.voice.file_id; }
else if (msg.audio) { input_type = 'voice'; file_id = msg.audio.file_id; }
else if (msg.photo && msg.photo.length) {
  input_type = 'photo';
  file_id = msg.photo[msg.photo.length - 1].file_id; // largest resolution
}
return [{ json: {
  chat_id: msg.chat ? String(msg.chat.id) : null,
  input_type,
  file_id,
  text: msg.text || '',
  caption: msg.caption || '',
} }];"""


def media_request_code(prompt_text, default_mime):
    """Code node: turn the downloaded Telegram file into a Gemini generateContent body."""
    return (
        "// Build a Gemini request with the downloaded file inlined as base64\n"
        "const bin = $input.first().binary.data;\n"
        "const buf = await this.helpers.getBinaryDataBuffer(0, 'data');\n"
        "let mime = bin.mimeType || '';\n"
        f"if (!mime || mime === 'application/octet-stream') mime = {json.dumps(default_mime)};\n"
        f"const instruction = {json.dumps(prompt_text, ensure_ascii=False)};\n"
        "return [{ json: { body: { contents: [ { parts: [\n"
        "  { text: instruction },\n"
        "  { inline_data: { mime_type: mime, data: buf.toString('base64') } },\n"
        "] } ] } } }];"
    )


BUILD_CONTEXT = r"""// Turn the Qdrant search results into numbered passages for the answer agent
const res = $input.first().json || {};
const points = (res.result && (res.result.points || res.result)) || [];
const parts = [];
for (const p of points) {
  const pl = p.payload || {};
  const text = pl.content || '';
  if (!text) continue;
  const m = pl.metadata || {};
  const header = '[' + (parts.length + 1) + '] (' + [m.procedure_id, m.source_type, m.date_retrieved, m.url]
    .map(x => x || '?').join(', ') + ')';
  parts.push(header + '\n' + text);
}
const r = $('Router').first().json.output;
const u = $('User input').first().json;
return [{ json: {
  context: parts.length ? parts.join('\n\n') : 'NO RELEVANT PASSAGES FOUND',
  n_chunks: parts.length,
  text: u.text,
  chat_id: u.chat_id,
  input_type: u.input_type,
  language: r.language,
  procedure: r.procedure || '',
} }];"""

GEMINI_TEXT = "$json.candidates[0].content.parts.map(p => p.text || '').join('').trim()"

# ------------------------------------------------------------ messages/text

WELCOME = ("Ahla! Nnajjem n3awnek fil procédures: carte d'identité (CIN), passeport, "
           "w ta2sis cherka fil RNE. Ab3athli so2alek b'texte, message vocal wala taswira mta3 document. / "
           "Je peux vous aider pour la CIN, le passeport et la création d'entreprise (RNE).")
UNSUPPORTED = ("Sorry, had el procédure mezelt ma na3rafhech. Taw nnajjem n3awnek ken fil CIN, "
               "passeport w ta2sis cherka (RNE). / Cette procédure n'est pas encore couverte : "
               "CIN, passeport et création d'entreprise (RNE) uniquement pour le moment.")
assert '"' not in WELCOME + UNSUPPORTED

REDIRECT_TEXT = (
    "={{ $json.output.needs_clarification && $json.output.clarification_question"
    " ? $json.output.clarification_question"
    " : ($json.output.category === \"unsupported_procedure\" ? \"" + UNSUPPORTED + "\" : \"" + WELCOME + "\") }}"
)

ANSWER_INPUT = ("=User message: {{ $json.text }}\n"
                "Language: {{ $json.language }}\n"
                "Procedure: {{ $json.procedure }}\n\n"
                "Context:\n{{ $json.context }}")

RETRY_INPUT = ("=User message: {{ $('Build context').first().json.text }}\n"
               "Language: {{ $('Build context').first().json.language }}\n"
               "Procedure: {{ $('Build context').first().json.procedure }}\n\n"
               "Context:\n{{ $('Build context').first().json.context }}\n\n"
               "A fact-checker found these claims in your previous draft NOT supported by the context: "
               "{{ $json.output.unsupported_claims.join(' ; ') }}\n"
               "Write the answer again without these claims. If the context lacks the information, say so.")

VERIFIER_INPUT = ("=Context:\n{{ $('Build context').first().json.context }}\n\n"
                  "Draft answer:\n{{ $json.text }}")

# Expressions with nested objects: never write two closing braces in a row inside them
EMBED_BODY = ("={{ JSON.stringify({ model: 'models/' + " + CFG + ".embed_model, "
              "content: { parts: [ { text: $json.output.search_query } ] }, "
              "taskType: 'RETRIEVAL_QUERY' }) }}")
SEARCH_BODY = ("={{ JSON.stringify({ query: $json.embedding.values, limit: 4, with_payload: true, "
               "filter: { must: [ { key: 'metadata.category', "
               "match: { value: $('Router').first().json.output.category } } ] } }) }}")

ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string",
                     "enum": ["identity", "business", "unsupported_procedure", "out_of_scope"]},
        "procedure": {"type": ["string", "null"]},
        "language": {"type": "string", "enum": ["french", "arabic", "darija_arabic_script",
                                                 "darija_latin", "english", "mixed"]},
        "search_query": {"type": "string"},
        "needs_clarification": {"type": "boolean"},
        "clarification_question": {"type": ["string", "null"]},
    },
    "required": ["category", "procedure", "language", "search_query",
                 "needs_clarification", "clarification_question"],
}

VERIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": ["verdict", "unsupported_claims", "notes"],
}


def build_chat():
    X = 260
    media_url = "=" + GEMINI_BASE + "{{ " + CFG + ".media_model }}:generateContent"
    n = [
        node("Telegram Trigger", "n8n-nodes-base.telegramTrigger", 1.2, [0, 300],
             {"updates": ["message"], "additionalFields": {}}),
        set_node("Config", [X, 300], [
            ("qdrant_url", "REPLACE_WITH_YOUR_QDRANT_URL_ENDING_WITH_6333"),
            ("collection", COLLECTION),
            ("embed_model", EMBED_MODEL),
            ("media_model", MAIN_MODEL),
        ]),
        node("Detect input", "n8n-nodes-base.code", 2, [2 * X, 300], {"jsCode": DETECT_INPUT}),
        node("Route by type", "n8n-nodes-base.switch", 3.2, [3 * X, 300], {
            "mode": "expression", "numberOutputs": 3,
            "output": "={{ $json.input_type === 'voice' ? 0 : ($json.input_type === 'photo' ? 1 : 2) }}",
        }),
        # voice branch
        telegram_get_file("Download voice", [4 * X, 40]),
        node("Prepare audio request", "n8n-nodes-base.code", 2, [5 * X, 40],
             {"jsCode": media_request_code(prompt("transcribe"), "audio/ogg")}),
        http_post("Gemini: transcribe", [6 * X, 40], media_url, "={{ JSON.stringify($json.body) }}"),
        set_node("Voice to text", [7 * X, 40], [("text", "={{ " + GEMINI_TEXT + " }}")]),
        # photo branch
        telegram_get_file("Download photo", [4 * X, 260]),
        node("Prepare photo request", "n8n-nodes-base.code", 2, [5 * X, 260],
             {"jsCode": media_request_code(prompt("vision"), "image/jpeg")}),
        http_post("Gemini: describe photo", [6 * X, 260], media_url, "={{ JSON.stringify($json.body) }}"),
        set_node("Photo to text", [7 * X, 260], [(
            "text",
            "={{ ($('Detect input').first().json.caption ? $('Detect input').first().json.caption + '\\n' : '')"
            " + '[Photo] ' + " + GEMINI_TEXT + " }}",
        )]),
        # all branches meet here
        set_node("User input", [8 * X, 300], [
            ("text", "={{ $json.text }}"),
            ("chat_id", "={{ $('Detect input').first().json.chat_id }}"),
            ("input_type", "={{ $('Detect input').first().json.input_type }}"),
        ]),
        llm_chain("Router", [9 * X, 300], "={{ $json.text }}", prompt("router"), parser=True),
        chat_model("Router model", [9 * X - 60, 520], LIGHT_MODEL, 0),
        parser("Router parser", [9 * X + 100, 520], ROUTER_SCHEMA),
        if_true("Answerable?", [10 * X, 300],
                "={{ ['identity', 'business'].includes($json.output.category) && !$json.output.needs_clarification }}"),
        telegram_send("Reply: clarify or redirect", [11 * X, 520],
                      "={{ $('User input').first().json.chat_id }}", REDIRECT_TEXT),
        http_post("Embed query", [11 * X, 200],
                  "=" + GEMINI_BASE + "{{ " + CFG + ".embed_model }}:embedContent", EMBED_BODY),
        http_post("Search Qdrant", [12 * X, 200],
                  "={{ " + CFG + ".qdrant_url }}/collections/{{ " + CFG + ".collection }}/points/query",
                  SEARCH_BODY),
        node("Build context", "n8n-nodes-base.code", 2, [13 * X, 200], {"jsCode": BUILD_CONTEXT}),
        llm_chain("Answer agent", [14 * X, 200], ANSWER_INPUT, prompt("answer")),
        chat_model("Answer model", [14 * X, 420], MAIN_MODEL, 0.2),
        llm_chain("Verifier", [15 * X, 200], VERIFIER_INPUT, prompt("verifier"), parser=True),
        chat_model("Verifier model", [15 * X - 60, 420], LIGHT_MODEL, 0),
        parser("Verifier parser", [15 * X + 100, 420], VERIFIER_SCHEMA),
        if_true("Verified?", [16 * X, 200], "={{ $json.output.verdict === 'pass' }}"),
        telegram_send("Send answer", [17 * X, 80],
                      "={{ $('Build context').first().json.chat_id }}",
                      "={{ $('Answer agent').first().json.text }}"),
        llm_chain("Answer agent (retry)", [17 * X, 320], RETRY_INPUT, prompt("answer")),
        chat_model("Retry model", [17 * X, 540], MAIN_MODEL, 0.2),
        telegram_send("Send corrected answer", [18 * X, 320],
                      "={{ $('Build context').first().json.chat_id }}", "={{ $json.text }}"),
    ]

    def main(*targets):
        return [[{"node": t, "type": "main", "index": 0} for t in out] for out in targets]

    def sub(kind, target):
        return {kind: [[{"node": target, "type": kind, "index": 0}]]}

    connections = {
        "Telegram Trigger": {"main": main(["Config"])},
        "Config": {"main": main(["Detect input"])},
        "Detect input": {"main": main(["Route by type"])},
        "Route by type": {"main": main(["Download voice"], ["Download photo"], ["User input"])},
        "Download voice": {"main": main(["Prepare audio request"])},
        "Prepare audio request": {"main": main(["Gemini: transcribe"])},
        "Gemini: transcribe": {"main": main(["Voice to text"])},
        "Voice to text": {"main": main(["User input"])},
        "Download photo": {"main": main(["Prepare photo request"])},
        "Prepare photo request": {"main": main(["Gemini: describe photo"])},
        "Gemini: describe photo": {"main": main(["Photo to text"])},
        "Photo to text": {"main": main(["User input"])},
        "User input": {"main": main(["Router"])},
        "Router": {"main": main(["Answerable?"])},
        "Answerable?": {"main": main(["Embed query"], ["Reply: clarify or redirect"])},
        "Embed query": {"main": main(["Search Qdrant"])},
        "Search Qdrant": {"main": main(["Build context"])},
        "Build context": {"main": main(["Answer agent"])},
        "Answer agent": {"main": main(["Verifier"])},
        "Verifier": {"main": main(["Verified?"])},
        "Verified?": {"main": main(["Send answer"], ["Answer agent (retry)"])},
        "Answer agent (retry)": {"main": main(["Send corrected answer"])},
        "Router model": sub("ai_languageModel", "Router"),
        "Router parser": sub("ai_outputParser", "Router"),
        "Answer model": sub("ai_languageModel", "Answer agent"),
        "Verifier model": sub("ai_languageModel", "Verifier"),
        "Verifier parser": sub("ai_outputParser", "Verifier"),
        "Retry model": sub("ai_languageModel", "Answer agent (retry)"),
    }
    return {"name": "TN procedures assistant - chat (Gemini)", "nodes": n, "connections": connections,
            "settings": {"executionOrder": "v1"}, "pinData": {}, "active": False}


def build_error():
    n = [
        node("Error Trigger", "n8n-nodes-base.errorTrigger", 1, [0, 300], {}),
        telegram_send(
            "Alert team", [260, 300], "REPLACE_WITH_TEAM_CHAT_ID",
            "=Workflow error in {{ $json.workflow.name }}\nNode: {{ $json.execution.lastNodeExecuted }}\n"
            "Error: {{ $json.execution.error.message }}\nExecution: {{ $json.execution.id }}"),
    ]
    return {"name": "TN procedures assistant - error alerts", "nodes": n,
            "connections": {"Error Trigger": {"main": [[{"node": "Alert team", "type": "main", "index": 0}]]}},
            "settings": {"executionOrder": "v1"}, "pinData": {}, "active": False}


def validate(wf):
    names = [x["name"] for x in wf["nodes"]]
    assert len(names) == len(set(names)), "duplicate node names"
    for src, kinds in wf["connections"].items():
        assert src in names, f"unknown source {src}"
        for outputs in kinds.values():
            for out in outputs:
                for c in out:
                    assert c["node"] in names, f"unknown target {c['node']}"
    connected = set(wf["connections"]) | {c["node"] for k in wf["connections"].values()
                                          for o in k.values() for out in o for c in out}
    orphans = set(names) - connected
    assert not orphans, f"unconnected nodes: {orphans}"

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str) and v.startswith("="):
            assert v.count("{{") == v.count("}}"), f"unbalanced expression: {v[:80]}"
    for nd in wf["nodes"]:
        walk(nd["parameters"])


if __name__ == "__main__":
    for fname, wf in [("chat_workflow.json", build_chat()), ("error_workflow.json", build_error())]:
        validate(wf)
        with open(os.path.join(ROOT, "n8n", fname), "w", encoding="utf-8") as f:
            json.dump(wf, f, ensure_ascii=False, indent=2)
        print(f"wrote n8n/{fname} ({len(wf['nodes'])} nodes)")
