# Writing a golden suite

A suite is a list of cases. Each case is one thing you need the model to keep
doing after the upgrade.

## The case format

JSONL, one case per line. Only `input` is required.

```json
{
  "id": "extract-invoice-1",
  "tags": ["extraction", "billing"],
  "system": "You extract structured data. Return only JSON.",
  "input": "Invoice #4471, total $1,240.00, due 2026-03-01",
  "expected": {"invoice_id": "4471", "total": 1240.0},
  "schema": {
    "type": "object",
    "properties": {
      "invoice_id": {"type": "string"},
      "total": {"type": "number"}
    },
    "required": ["invoice_id", "total"]
  },
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "lookup_invoice",
        "description": "Look up an invoice",
        "parameters": {
          "type": "object",
          "properties": {"invoice_id": {"type": "string"}},
          "required": ["invoice_id"]
        }
      }
    }
  ],
  "expect_tool": "lookup_invoice",
  "params": {"temperature": 0, "max_tokens": 400},
  "meta": {"owner": "billing-team"}
}
```

| Field | | |
|---|---|---|
| `id` | optional | Stable hash generated if absent. Use explicit ids so PR comments stay readable across runs. |
| `tags` | optional | Drives per-tag thresholds and the tag breakdown. |
| `system` | optional | System prompt. |
| `input` | **required** | A string, or a `[{role, content}]` array for multi-turn. |
| `expected` | optional | `"str"` (containment) · `["a","b"]` (any-of) · `{json subset}` · `{"regex": "..."}` |
| `schema` | optional | JSON Schema the output must satisfy. |
| `tools` | optional | OpenAI-style tool definitions. |
| `expect_tool` | optional | The tool that should be selected. |
| `params` | optional | Per-case `temperature`, `max_tokens`, `seed`. |
| `meta` | optional | Free-form; carried into the report. |

### Expectation forms

```jsonc
"expected": "Paris"                       // substring containment
"expected": ["Paris", "Lyon"]             // any-of
"expected": {"status": "ok", "n": 3}      // JSON subset (extra keys allowed)
"expected": {"regex": "(?i)\\bparis\\b"}  // regex
```

A JSON subset is the right choice when you care about *some* fields and not
others — it will not fail because the model added a helpful extra key.

## Loading formats

`--suite` accepts a file or a directory of any of these:

| Format | Notes |
|---|---|
| **JSONL** | The native format. |
| **JSON** | An array of case objects. |
| **YAML** | An array of case objects. |
| **CSV** | Columns `id,input,expected,tags,system`. |
| **promptfoo** | `prompts` + `tests`; `contains`/`icontains`/`equals` → `expected`; `is-json` schema → `schema`. `javascript` and `llm-rubric` asserts are skipped **with a warning**. |
| **DeepEval / OpenAI Evals** | JSONL with `input` / `expected_output`. |
| **OTel / Langfuse traces** | Via `suite from-traces`, with PII redaction. |

Unsupported asserts are never silently dropped — they are reported as suite
warnings and printed in the report.

## What makes a *good* migration case

A suite of 300 random prompts is worth less than 30 pointed ones. A good migration
case has four properties:

### 1. It has a right answer you can check cheaply

Prefer cases a deterministic metric can grade: a schema, an expected substring, an
expected tool. Reserve the judge for genuinely subjective cases.

### 2. It sits on a surface providers actually break

In rough order of how often migrations break them:

1. **Structured output** — dropped keys, extra prose, fenced JSON
2. **Tool selection** — a different tool, or none
3. **Refusals** — the new model is more cautious
4. **Verbosity** — same content, 3× the tokens, 3× the cost
5. **Instruction following** — format and length constraints
6. **Language** — a non-English prompt answered in English
7. **Truncation** — hitting `max_tokens` where the old model did not

Write at least a couple of cases for each surface you depend on.

### 3. It is stable under sampling

Run `mock:stable → mock:stable`-style control (or the same real model twice). If a
case drifts against *itself*, it is too noisy to be a migration gate — tighten the
prompt or add `temperature: 0`.

### 4. It is tagged

Tags drive per-tag thresholds. `tool-use` and `legal` usually deserve stricter
limits than the suite average.

## Validating

```console
$ modelbump suite validate suite.jsonl

  cases:      18
  with schema: 4
  with tools:  3
  with expect: 12
  multi-turn:  2
  largest:     1,842 chars
  tags:        extraction(3), classification(2), ...

  ✓ suite is valid
```

`validate` reports unknown fields, duplicate ids, invalid JSON Schemas, and
oversized inputs. Run it in CI alongside the diff.

## Stats

```console
$ modelbump suite stats suite.jsonl
```

Prints the tag histogram and coverage so you can see which surfaces are thin.

## Sizing

| Suite size | Typical runtime (3 samples, 2 models) |
|---:|---|
| 50 cases | ~1 min |
| 300 cases | < 10 min |

The spec's target is one command → verdict + report in under 10 minutes for a
300-case suite.

## Per-tag thresholds

```toml
[thresholds]
max_drift_rate = 0.25

[thresholds.tag."tool-use"]
max_drift_rate = 0.10
max_expected_hit_drop = 0.0
```

See [thresholds & CI](thresholds-and-ci.md).
