# Reports

Every run writes three artifacts, plus an optional fourth for CI.

| File | Purpose |
|---|---|
| `report.json` | Versioned machine-readable schema. The source of truth. |
| `report.md` | PR-comment-safe Markdown. |
| `report.html` | A single self-contained interactive file. |
| `report.xml` | Optional JUnit XML for CI test tabs. |

## report.json

```jsonc
{
  "schema_version": "1.0",
  "modelbump": {"version": "0.1.0"},
  "generated_at": "2026-01-01T00:00:00Z",

  "from": {"spec": "mock:stable", "provider": "mock", "model": "stable"},
  "to":   {"spec": "mock:drifty", "provider": "mock", "model": "drifty"},

  "suite": {"path": "suite.jsonl", "cases": 18, "warnings": []},
  "samples": 3,
  "params": {"temperature": null, "drift_threshold": 0.35, "concurrency": 8},

  "thresholds": {"max_drift_rate": 0.25, "...": "..."},
  "tag_thresholds": {"tool-use": {"max_drift_rate": 0.1}},

  "judge_model": null,
  "rubric_hash": "640969daf7e11e8f",
  "structured_output": {"a": "mock-json_schema", "b": "mock-json_schema"},

  "summary": {
    "total_cases": 18,
    "flagged_cases": 16,
    "drift_rate": 0.888,
    "mean_drift_score": 0.568,
    "max_drift_score": 0.969,
    "flag_histogram": {"expected": 9, "semantic": 6},
    "flag_rules": {"expected": "A hit the expectation, B did not", "...": "..."},
    "a": {"schema_valid_rate": 1.0, "expected_hit_rate": 1.0, "p50_latency_ms": 144},
    "b": {"schema_valid_rate": 0.667, "expected_hit_rate": 0.451, "p50_latency_ms": 215},
    "tags": [{"tag": "extraction", "cases": 2, "flagged_cases": 2, "drift_rate": 1.0}],
    "worst_cases": ["extract-1", "tool-2", "..."],
    "judge": null
  },

  "cases": [
    {
      "id": "extract-1",
      "tags": ["extraction"],
      "kind": "json",
      "flags": ["schema", "expected", "semantic"],
      "flag_details": {"schema": "schema-valid rate 100.0% → 0.0%"},
      "drift_score": 0.97,
      "cross_similarity": 0.20,
      "noise_floor": 0.67,
      "judge": null,
      "a": {"outputs": ["..."], "sample_metrics": ["..."], "tool_calls": []},
      "b": {"outputs": ["..."], "sample_metrics": ["..."], "tool_calls": []}
    }
  ],

  "registry": {"source": "bundled-snapshot"},
  "reproduce": {
    "command": "modelbump diff --from mock:stable --to mock:drifty --suite suite.jsonl",
    "command_hash": "…",
    "config_hash": "…",
    "rubric_hash": "640969daf7e11e8f",
    "cache_hits": 18,
    "cache_misses": 18,
    "projected_cost_usd": 0.007,
    "elapsed_s": 0.007
  },

  "verdict": {
    "pass": false,
    "violations": [
      {
        "rule": "max_drift_rate",
        "message": "drift rate 88.9% exceeds max 25.0% (16/18 cases flagged)",
        "observed": 0.888,
        "limit": 0.25,
        "scope": "suite",
        "severity": "violation"
      }
    ],
    "warnings": []
  }
}
```

`schema_version` is bumped when the shape changes. `report` and `compare` read
this file, so it is the durable artifact — keep it, not just the HTML.

## report.md

PR-comment-safe: no HTML, no images, no hover. It opens with a sticky marker so
the GitHub Action can find and update its own comment:

```markdown
<!-- modelbump-report -->
# modelbump — mock:stable → mock:drifty

❌ **FAIL**  ·  **16/18** cases flagged  ·  drift rate **88.9%**  ·  3 samples/case

## ❌ Threshold violations
...
```

## report.html

A **single self-contained file**. Inline CSS and JS, no external assets, no
network — safe to attach to a PR, email, or artifact.

| Feature | |
|---|---|
| Filter by flag | Click a flag chip |
| Filter by tag | Click a tag chip |
| Side-by-side outputs | Per case, per sample |
| Per-sample toggles | Step through samples A and B |
| Light / dark | Toggle in the header, persisted to `localStorage` |
| Copyable reproduce command | One click |

The report embeds the case data as JSON, so filtering is instant and works
offline.

## report.xml (JUnit)

```console
$ modelbump diff ... --junit out/report.xml
```

Each case becomes a `<testcase>` named after the case id. A flagged case becomes a
`<failure>` with the flags as the message, so CI test tabs render the drift
directly.

## Re-rendering

```console
$ modelbump report out            # re-render md + html from report.json
$ modelbump report out --format md
```

Useful after a template tweak — no model calls, no cost.

## Comparing two runs

```console
$ modelbump compare before/report.json after/report.json
```

Answers the question you actually have after a prompt fix: **did drift change?**
It reports per-case movement — cases that improved, cases that regressed, cases
that flipped in and out of the flagged set.

## Reproducibility

Every report embeds the command line, config hash, rubric hash, tool version,
cache-hit count, the structured-output mechanism each provider used, and a
"how to reproduce" block. A report is a citation, not a screenshot: someone else
can re-run it and get the same numbers.
