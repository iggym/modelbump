# Quickstart

Everything in this page runs **offline**, with no API keys. The `mock:` provider
is deterministic, so you will get the same numbers.

## Install

```console
$ pipx install modelbump
```

Or with pip:

```console
$ pip install modelbump
```

Python ≥ 3.11.

## Scaffold a project

```console
$ modelbump init
```

This writes two files:

| File | |
|---|---|
| `suite.jsonl` | An 18-case example suite covering extraction, classification, summarization, tool use, multi-turn, safety, reasoning, formatting, long context, nested schemas, non-English, and JSON-subset expectations. |
| `modelbump.toml` | Thresholds, with a commented per-tag example. |

## A model change that is safe

The same model on both sides must drift by zero. This is the control condition.

```console
$ modelbump diff --from mock:stable --to mock:stable --suite suite.jsonl
```

```text
   ✅ PASS   0/18 cases flagged   drift rate 0.0%

  flags: (none)
```

## A model change that breaks

`mock:drifty` behaves like a plausible-but-wrong upgrade: it drops JSON keys,
rewords prose, picks the wrong tool, and occasionally refuses.

```console
$ modelbump diff --from mock:stable --to mock:drifty --suite suite.jsonl
```

```text
  drifted: 16/18 cases  ( 88.9% drift rate)

   ❌ FAIL   6 violation(s)
    • max_drift_rate: drift rate 88.9% exceeds max 25.0% (16/18 cases flagged)
    • min_schema_valid_rate: model B schema-valid rate 66.7% is below floor 98.0%
    • max_schema_valid_drop: schema-valid rate dropped 33.3%, max drop 2.0%
    • max_refusal_increase: refusal rate increased 11.1%, max increase 5.0%

reports written:
  out/report.json
  out/report.md
  out/report.html
```

The command exits **1**, so CI fails.

## Read the report

Open `out/report.html` in a browser. It is a single self-contained file: filter
by flag or tag, toggle per-sample outputs, switch light/dark, and copy the
reproduce command.

Prefer the terminal? Re-render the Markdown:

```console
$ modelbump report out --format md
```

## Run against real models

Drop the `mock:` prefix and add a key:

```console
$ export OPENAI_API_KEY=...
$ modelbump diff --from gpt-4.1 --to gpt-5 --suite suite.jsonl
```

See [model spec & providers](providers.md) for the grammar and the full env-var
table, and [cost control](cost-control.md) before you run something large.

## Where to go next

- ✍️ [Writing a golden suite](writing-a-suite.md) — make the suite *yours*
- 🌊 [How drift is measured](how-drift-is-measured.md) — what the numbers mean
- 🚦 [Thresholds & CI](thresholds-and-ci.md) — wire it into a workflow
