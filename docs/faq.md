# FAQ

### Is this a leaderboard or a quality score?

No. `modelbump` never produces an absolute quality number. It measures the
**difference** between two versions on your workload. A model can be excellent and
still fail your gate, if it changed something you depend on.

### Why not just run my existing evals twice and compare pass rates?

You can, and you should — `modelbump` will happily consume promptfoo and DeepEval
suites. What you get on top is the part that makes the comparison trustworthy:

- a **noise floor** (self-similarity) so sampling variance is not reported as drift
- drift computed per case, with similarity appropriate to the output kind
- structured-output and tool-call surfaces, not just pass/fail
- a reproducible report and a CI verdict

### Why is drift 0 for the same model?

Because `cross_similarity == noise`, so `max(0, noise − noise) / noise == 0`. That
is the definition, and it is tested. If you ever see nonzero drift for an identical
model, that is a bug — please file it.

### My case drifts against itself. Is that a bug?

No — it means the case is too noisy to be a migration gate. Raise `--samples`,
set `temperature: 0` on the case, or make the prompt more constrained. A case whose
own samples disagree cannot tell you whether a model changed.

### Do I need API keys to try it?

No. `modelbump init` then `modelbump diff --from mock:stable --to mock:drifty` runs
the entire pipeline offline, deterministically. That is how the docs, the tests,
and CI work.

### Which output format should I keep?

Keep `report.json`. It is versioned and is what `report` and `compare` read. The
HTML is a rendering; the JSON is the record.

### Does it call a judge on every case?

No. By default the judge runs **only on flagged cases**, because if the cheap
metrics already agree there is nothing for a judge to add. Use `--judge-all` if
you really want everything judged, and `--judge-max-cases` to cap it.

### What happens when a provider returns an error?

Errors are recorded as `error` metrics, counted in `error_rate`, and drive the
`errors` flag. Errors are **not cached**, so a transient failure is retried on the
next run. `--max_error_rate` gates on it.

### What if a model is not in the registry?

Cost is reported as `null` — never zero. Provider inference may fail, in which case
pass it explicitly: `openai:some-new-model`.

### Can I gate a PR on this?

Yes — that is the point. `diff` exits `1` on any violation, and the bundled action
posts a sticky PR comment and uploads the HTML report. See
[thresholds & CI](thresholds-and-ci.md).

### Does it send anything anywhere?

No telemetry, no accounts, no hosted service. It talks to model providers and
nothing else. The cache is a local directory.

### Can I compare more than two models at once?

Not in v0.1. A `--to` matrix is on the post-0.1 backlog. For now, run `diff` twice
and use `compare` to see the movement between runs.

### Is there an embedding-drift mode?

Not yet. Embedding-model drift (nearest-neighbour overlap on a corpus) is planned
for after 0.1.

### How do I cite a report?

Every report embeds the command line, config hash, rubric hash, tool version,
cache-hit count, and the structured-output mechanism per provider. Copy the
`reproduce` block out of `report.json` — it is designed to be pasted into a PR or a
blog post and re-run by someone else.
