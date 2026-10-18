# Model information and terms

- **Models:** `gemma-4-26b-a4b-it` (default, mixture-of-experts, ~4B active parameters) and `gemma-4-31b-it`,
  both used as hosted models through the Gemini API with the official `google-genai` Python SDK.
- **Access:** requires your own Gemini API key (`GEMINI_API_KEY`). Usage is subject to the
  [Gemini API Terms of Service](https://ai.google.dev/gemini-api/terms) and quota/pricing of your account.
- **Gemma terms:** Gemma models are provided by Google under their own Gemma terms of use and prohibited-use
  policy (see <https://ai.google.dev/gemma/terms>). These terms are separate from, and not replaced by, OSS
  Forge's Apache-2.0 license, which covers only this repository's source code.
- **No weights:** OSS Forge never downloads, bundles or redistributes model weights.
- **Data sent to the API:** issue text, selected code definitions, generated tests and test-output tails,
  after secret redaction. Do not run OSS Forge on code you are not allowed to send to a hosted API.
