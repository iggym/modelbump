# Thresholds & CI

## The threshold set

Defaults live in `modelbump.toml`:

```toml
[thresholds]
max_drift_rate          = 0.25   # ≤ 25% of cases may drift
min_schema_valid_rate   = 0.98   # B must satisfy schemas at least 98% of the time
max_schema_valid_drop   = 0.02   # B may not drop more than 2 points of schema validity
max_expected_hit_drop   = 0.05   # B may not drop more than 5 points of expected hits
max_refusal_increase    = 0.05   # refusals may not rise by more than 5 points
max_error_rate          = 0.02   # B's error rate ceiling
max_cost_increase       = 1.0    # B may cost up to 2× A
max_latency_increase    = 1.0    # B may be up to 2× A's p50
max_judge_prefer_a_rate = 0.6    # the judge may prefer A on at most 60% of judged cases
```

| Threshold | Rule |
|---|---|
| `max_drift_rate` | `drift_rate ≤ max_drift_rate` |
| `min_schema_valid_rate` | `schema_valid_rate(B) ≥ floor` |
| `max_schema_valid_drop` | `schema_valid_rate(A) − schema_valid_rate(B) ≤ max` |
| `max_expected_hit_drop` | `expected_hit_rate(A) − expected_hit_rate(B) ≤ max` |
| `max_refusal_increase` | `refusal_rate(B) − refusal_rate(A) ≤ max` |
| `max_error_rate` | `error_rate(B) ≤ max` |
| `max_cost_increase` | `cost(B) ≤ cost(A) × (1 + max)` |
| `max_latency_increase` | `p50(B) ≤ p50(A) × (1 + max)` |
| `max_judge_prefer_a_rate` | fraction of judged cases preferring A ≤ max |

## Per-tag overrides

Different surfaces deserve different bars. Tool-calling breaks more often than
prose, so hold it tighter:

```toml
[thresholds]
max_drift_rate = 0.25

[thresholds.tag."tool-use"]
max_drift_rate = 0.10
max_expected_hit_drop = 0.0

[thresholds.tag."legal"]
max_drift_rate = 0.05
```

A tag override **replaces** the suite-level value for cases carrying that tag.
Violations are reported with the tag in the rule name:

```
• tag.tool-use.max_drift_rate: tag 'tool-use' drift rate 100.0% exceeds its max 10.0% (2/2 cases)
```

## Exit codes

| Exit | Meaning |
|:--:|---|
| `0` | ✅ verdict PASS |
| `1` | ❌ one or more threshold violations |
| `2` | 🛑 usage error, or `--max-cost` budget exceeded |

`--strict` promotes warnings to violations, so a run that would have exited `0`
with warnings exits `1`.

## Violations are printed with the numbers

```
   ❌ FAIL   6 violation(s)
    • max_drift_rate: drift rate 88.9% exceeds max 25.0% (16/18 cases flagged)
    • min_schema_valid_rate: model B schema-valid rate 66.7% is below floor 98.0%
    • max_schema_valid_drop: schema-valid rate dropped 33.3% (A 100.0% → B 66.7%), max drop 2.0%
    • max_refusal_increase: refusal rate increased 11.1% (A 0.0% → B 11.1%), max increase 5.0%
```

Each violation carries `rule`, `message`, `observed`, `limit`, `scope`, and
`severity` in `report.json`, so a bot can act on it without parsing prose.

## GitHub Actions

### Plain workflow

```yaml
name: model-migration
on: pull_request

jobs:
  modelbump:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.13"}
      - run: pipx install modelbump
      - run: modelbump diff --from gpt-4.1 --to gpt-5 --suite suite.jsonl --out out
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
      - uses: actions/upload-artifact@v4
        if: always()
        with: {name: modelbump-report, path: out/}
```

### The bundled action

```yaml
- uses: iggym/modelbump@v0.1.0
  with:
    from: gpt-4.1
    to: gpt-5
    suite: suite.jsonl
    samples: "3"
    judge: gpt-5-mini
    thresholds: modelbump.toml
    max-cost: "2.00"
```

| Input | Default | |
|---|---|---|
| `from` | — | Baseline model spec |
| `to` | — | Candidate model spec |
| `suite` | `suite.jsonl` | Suite path |
| `samples` | `3` | Samples per case per model |
| `judge` | `""` | Judge model spec (empty disables) |
| `thresholds` | `""` | Path to a `modelbump.toml` |
| `max-cost` | `""` | Budget guard in USD |
| `comment` | `true` | Post/update a sticky PR comment |
| `fail-on-violation` | `true` | Fail the job on FAIL |

| Output | |
|---|---|
| `pass` | `"true"` / `"false"` |
| `drift_rate` | Fraction of cases flagged |
| `report` | Path to `report.html` |

It writes the Markdown report into the job summary, uploads the reports as an
artifact, and updates **one** sticky PR comment (matched by the
`<!-- modelbump-report -->` marker) instead of adding a new one each push.

## Tuning advice

- Start with the defaults. They are deliberately permissive.
- If a case drifts against itself, fix the case rather than loosening the threshold.
- Tighten `tool-use` and anything legally load-bearing.
- Leave `max_cost_increase` generous at first — a better model often costs more,
  and that is a decision for a human, not a gate.
