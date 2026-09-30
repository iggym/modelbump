# Model spec & providers

## The grammar

```text
[provider:]model[@base_url]
```

| Example | Resolves to |
|---|---|
| `gpt-4.1` | provider inferred from the registry → `openai` |
| `openai:gpt-4.1` | explicit |
| `anthropic:claude-3-5-sonnet-20241022` | Anthropic Messages API |
| `google:gemini-2.0-flash` | Gemini `generateContent` |
| `azure:my-deployment@https://acme.openai.azure.com` | Azure OpenAI, with `api-version` |
| `bedrock:anthropic.claude-3-5-sonnet-20241022-v2:0` | AWS Bedrock Converse (SigV4) |
| `ollama:llama3.1@http://localhost:11434/v1` | OpenAI-compatible endpoint |
| `mock:stable` | deterministic, offline |

When the provider is omitted it is inferred from the model name via the
`surfacelock` registry. If the registry does not know the name, pass the provider
explicitly.

## Environment variables

```console
$ modelbump providers
```

prints this table with masked values, so you can see what the current shell has:

| Provider | Env var |
|---|---|
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| Google Gemini | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |
| Azure OpenAI | `AZURE_OPENAI_API_KEY` |
| AWS Bedrock | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`, or a named profile |
| OpenAI-compatible | `OPENAI_API_KEY`, or whatever the endpoint expects |

Keys are never printed. `providers` shows only whether a key is present and a
masked form.

## Transports

All adapters speak raw HTTP over `httpx`. There are **no vendor SDKs**.

### OpenAI — Chat Completions and Responses

`modelbump` auto-selects the transport:

- **Responses API** for `gpt-5*` and `o*` model families
- **Chat Completions** otherwise

Override per side when the heuristic is wrong:

```console
$ modelbump diff --from gpt-4.1 --to gpt-5 --api responses ...
$ modelbump diff ... --api-from chat --api-to responses
```

### Structured output

Structured output goes through each provider's **native** mechanism:

| Provider | Mechanism |
|---|---|
| OpenAI | `response_format: {type: "json_schema", ...}` |
| Google Gemini | `generationConfig.responseSchema` (unsupported keywords stripped) |
| Anthropic | tool-forcing (a synthetic tool named for the schema) |
| Mock | `mock-json_schema` |

**The mechanism used is recorded in the report** under `structured_output`, per
side:

```json
"structured_output": {"a": "json_schema", "b": "responseSchema"}
```

This matters: changing transports during a migration can change the *mechanism*
even when the model is the same, and the mechanism itself is a migration variable.

### Tool translation

Tools are accepted in OpenAI style and translated per provider. When a translation
cannot preserve something — an unsupported JSON Schema keyword, for example — the
warning is recorded rather than dropped silently:

```
⚠ tool 'search' uses 'patternProperties', unsupported by this provider — dropped
```

## Retries and rate limits

Every adapter retries on `408`, `409`, `429`, `5xx`, `529`, and network errors,
with exponential backoff and jitter, honouring `Retry-After`.

Control concurrency and throughput:

```console
$ modelbump diff ... --concurrency 8      # default 8
$ modelbump diff ... --rpm 600            # requests per minute
$ modelbump diff ... --tpm 200000         # tokens per minute
```

## The `mock:` provider

Deterministic, offline, and used by the docs, the tests, and CI. The same case
always produces the same output for a given variant, which is what makes
"identical model ⇒ zero drift" a *testable* invariant.

| Variant | Behaviour |
|---|---|
| `stable` | Faithful to the expectation. Drift 0. |
| `drifty` | Drops JSON keys, rewords prose, picks the wrong tool, sometimes refuses. |
| `verbose` | Correct content, ~2.5× length. Length flag only. |
| `strict-json` | Clean, fence-free JSON matching the schema. |
| `refusenik` | Refuses every prompt. |
| `broken` | HTTP 500 on every call. |
| `slow` | Adds a fixed 400 ms delay. |

```console
$ modelbump diff --from mock:stable --to mock:verbose --suite suite.jsonl
$ modelbump diff --from mock:stable --to mock:refusenik --suite suite.jsonl
$ modelbump diff --from mock:stable --to mock:broken --suite suite.jsonl
```

Use them to test your CI wiring before spending a cent.

## Offline behaviour

Without `surfacelock` installed, `modelbump` falls back to a bundled registry
snapshot for pricing, retirements, and name→provider inference. Install
`modelbump[registry]` to get live data.

Unknown models yield cost `null` — **never a misleading zero**.
