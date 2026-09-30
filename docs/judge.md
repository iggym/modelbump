# Judge & rubric

The judge is optional and bounded. It exists for the cases that cheap metrics
cannot grade: is this summary as good as that one? Did the reworded answer lose
meaning?

```console
$ modelbump diff --from gpt-4.1 --to gpt-5 --suite suite.jsonl --judge gpt-5-mini
```

## It runs only where it matters

By default the judge runs **only on flagged cases**. If the cheap metrics already
agree, there is nothing for a judge to add, and you should not pay for a second
opinion you will ignore.

```console
$ modelbump diff ... --judge-all            # judge everything
$ modelbump diff ... --judge-max-cases 50   # hard cap on judge calls
```

## Position swap

LLM judges have a well-documented position bias: they tend to prefer whichever
output appears first. `modelbump` cancels it by asking **both** ways and comparing:

```
Order 1:   A first, B second   →  {equivalent, better, reason}
Order 2:   B first, A second   →  {equivalent, better, reason}
```

The `better` field is translated back into A/B terms for each order. Then:

| Outcome | Verdict |
|---|---|
| Orders agree | The verdict stands |
| Orders disagree | 🟡 `tie` / `uncertain` — recorded, never silently resolved |

A judge that just prefers whatever it saw first produces disagreement, which is
recorded as uncertain. This is tested with a deliberately position-biased mock.

## What comes back

```json
{
  "equivalent": false,
  "better": "a",
  "reason": "B dropped the invoice total; A includes it.",
  "uncertain": false,
  "order1": {"equivalent": false, "better": "a", "reason": "..."},
  "order2": {"equivalent": false, "better": "a", "reason": "..."},
  "cost_usd": 0.00042
}
```

## Rubric

The rubric is versioned and hashed. `RUBRIC_VERSION` identifies the prompt
generation, and the hash of the actual rubric text is printed in every report:

```json
"rubric_hash": "640969daf7e11e8f"
```

Two reports with different rubric hashes are not directly comparable — that is the
point of printing it.

### Custom rubric

```console
$ modelbump diff ... --judge gpt-5-mini --rubric ./rubric.md
```

The hash of *your* file is what gets recorded. A rubric tuned to your domain is
usually worth more than the default.

## Cost

Judge calls are counted in the report's cost totals **and** in `--max-cost`:

```console
$ modelbump diff ... --judge gpt-5-mini --max-cost 2.00
```

## When not to use the judge

- If a case has a schema, an expected substring, or an expected tool — use the
  cheap metric. It is deterministic and free.
- If you need a gate that never flakes, prefer deterministic flags.
- If the whole suite is flagged, a judge will agree with you 300 times for money.

The judge is a scalpel for the handful of genuinely subjective cases, not a
general-purpose grader.
