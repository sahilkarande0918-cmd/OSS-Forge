# Live notes: hosted Gemma 4 behaviour (2026-10-18)

Observations from real calls to `gemma-4-26b-a4b-it` through the Gemini API (google-genai 2.31) while
building OSS Forge. They explain the provider's defaults.

## Structured output

Same diagnosis prompt (~4.3k input tokens; asks for hypotheses plus a full pytest module inside JSON),
temperature 1.0 / top_p 0.95 / top_k 64, two samples per configuration:

| configuration | result |
|---|---|
| `response_json_schema`, thinking minimal | 0/2 valid (1 repetition loop to `MAX_TOKENS`, 1 `RECITATION` stop) |
| `response_json_schema`, thinking high | 0/2 valid (both repetition loops to `MAX_TOKENS`, ~88 s each) |
| plain text + ```json template, thinking high | 1/2 valid first try; the other parsed but failed validation (handled by the repair step) — ~18 s each |
| plain text + template, thinking minimal | 2/2 HTTP 500 from the endpoint |
| JSON mime + template, thinking minimal | not measured (429 quota exhausted) |

On a short prompt (issue interpretation) `response_json_schema` validated first try, while JSON mime type
alone returned a list instead of an object, and a JSON Schema pasted into the prompt was echoed back as a
schema instead of an instance.

Earlier, with temperature 0.2, constrained decoding produced loops such as runs of underscores and a
repeated `# I'll use the text.` comment. Thinking text also leaked into the JSON (`...producing 3thought...`).

**Defaults chosen:** `OSS_FORGE_JSON_MODE=template` (free text + an example instance generated from the
Pydantic model, validated locally, one focused repair), `OSS_FORGE_THINKING=high`, Gemma's recommended
sampling, automatic resampling of truncated/looping replies (max 2), and the schema-echo unwrapping kept as
a safety net. `OSS_FORGE_JSON_MODE=schema` remains available.

These are small samples (n=2 per row) on one prompt; treat them as engineering evidence, not a benchmark.

## Quota

The key used hit `429 RESOURCE_EXHAUSTED` after roughly 25 requests in a few minutes of probing. The provider
honours the server's `retryDelay` and backs off exponentially. Keep `OSS_FORGE_MAX_API_CALLS` modest and
expect live benchmarks to take minutes.
