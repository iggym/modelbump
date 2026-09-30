# Importing suites

You already have tests somewhere. `modelbump` will take them.

```console
$ modelbump diff --from A --to B --suite path/to/anything
```

`--suite` accepts a **file or a directory** of any supported format.

## Native formats

| Format | Notes |
|---|---|
| **JSONL** | The native format — one case per line. |
| **JSON** | An array of case objects. |
| **YAML** | An array of case objects. |
| **CSV** | Columns `id,input,expected,tags,system`. |

## promptfoo

A `promptfooconfig.yaml` with `prompts` and `tests`:

```yaml
prompts:
  - "Answer: {{question}}"
tests:
  - vars: {question: "What is the capital of France?"}
    assert:
      - type: contains
        value: Paris
      - type: is-json
        value:
          type: object
          properties: {city: {type: string}}
```

| promptfoo assert | Becomes |
|---|---|
| `contains` / `icontains` / `equals` | `expected` |
| `is-json` with a schema | `schema` |
| `contains-json` | JSON-subset `expected` |
| `javascript` | ⚠️ skipped, with a warning |
| `llm-rubric` | ⚠️ skipped, with a warning |

Unsupported asserts are never silently dropped. They appear in `suite.warnings`
and are printed in the report, so you know exactly what did not carry over.

## DeepEval / OpenAI Evals

JSONL where each line has `input` and `expected_output`:

```json
{"input": "What is 2+2?", "expected_output": "4"}
```

Mapped to `input` and `expected`. Extra fields land in `meta`.

## Traces

```console
$ modelbump suite from-traces traces.jsonl --out cases.jsonl
```

Ingests:

| Source | Format |
|---|---|
| OpenTelemetry GenAI semconv | JSONL export |
| Langfuse | CSV or JSON export |

Cases are built from `input`, `system`, and `tools`, with `tags` derived from
metadata. This is the wedge of a fuller `trace2test` tool planned post-0.1: turn
production traffic into a migration suite.

### Sampling

```console
$ modelbump suite from-traces traces.jsonl --sample 200 --out cases.jsonl
```

### PII redaction

Redaction runs on the way in, before anything is written. It is regex-based and
documented:

| Entity | |
|---|---|
| 📧 Email addresses | `user@example.com` → `[EMAIL]` |
| 📞 Phone numbers | US and international forms → `[PHONE]` |
| 💳 Card numbers | 13–19 digits, with Luhn check → `[CARD]` |
| 🏦 IBANs | `DE89 3704 0044 0532 0130 00` → `[IBAN]` |
| 🪪 US SSNs | `123-45-6789` → `[SSN]` |

Redaction is tested: no raw PII survives the fixtures.

> Redaction is a safety net, not a compliance guarantee. Review an imported suite
> before committing it to a public repository.

## The result

Every import produces the same thing: native JSONL.

```console
$ modelbump suite from-traces traces.jsonl --out cases.jsonl
$ modelbump suite validate cases.jsonl
$ modelbump diff --from gpt-4.1 --to gpt-5 --suite cases.jsonl
```

From any input format, you land on the same pipeline.
