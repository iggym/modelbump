# How drift is measured

This page is the precise definition of the number `modelbump` reports. If a case
is flagged, you should be able to point at the arithmetic that flagged it.

## The wrong question

The naive question is _"did the outputs differ?"_. A model differs from **itself**
every time you sample it — temperature, tie-breaking, and provider nondeterminism
all move the output around. A method that flags any difference reports drift on an
unchanged model, which makes it useless as a CI gate.

## The right question

Drift is only real when the two models are **more different from each other than
each model is from itself**.

So we measure the noise floor first.

### 1. Self-similarity (the noise floor)

For each case and each model, take the `N` samples (default 3) and compute the
mean pairwise similarity **within** that set:

```
self_A = mean(similarity(s_i, s_j) for i < j in samples(A))
self_B = mean(similarity(s_i, s_j) for i < j in samples(B))
```

If a model gives you a different answer every time, `self_A` is low — its outputs
are noisy, and you should be correspondingly sceptical of any "drift".

### 2. Cross-similarity

Compare the two sets against each other:

```
cross = mean(similarity(a, b) for a in samples(A) for b in samples(B))
```

### 3. The drift score

```
noise = min(self_A, self_B)
drift = max(0, noise − cross) / noise
```

`drift` is in `[0, 1]`:

| `drift` | Meaning |
|---:|---|
| `0.0` | Indistinguishable — the models differ no more than each differs from itself |
| `0.35` | The default `--drift-threshold`: enough separation to call it a behavioral change |
| `1.0` | Maximally different given the noise floor |

### Why `min`?

Using the **minimum** of the two self-similarities is the conservative choice: the
noisiest model sets the bar. If A is rock-steady and B is all over the place, we
do not want B's chaos to look like drift.

## The invariant

If `A` and `B` are the same model, then `cross ≈ self_A ≈ self_B`, so:

```
noise = min(self, self) = self
drift = max(0, self − self) / self = 0
```

**Drift is exactly zero, and no flags fire.** This is tested — not asserted — in
`tests/test_drift.py`, across every mock variant.

## Similarity is kind-aware

Comparing prose and comparing JSON need different maths, so `modelbump` picks the
similarity function from the *output kind* of the case.

### 🗒️ Free text

```
similarity = 0.5 · normalized_sequence_ratio + 0.5 · token_jaccard
```

- **Normalized sequence ratio** — the longest-common-subsequence ratio, normalized
  by length. Catches reordering and insertion.
- **Token Jaccard** — `|A ∩ B| / |A ∪ B|` over token sets. Catches vocabulary
  churn that LCS is blind to.

Half-and-half, because each is blind to something the other sees.

### 🧱 JSON

Text similarity on JSON is misleading — `{"a":1,"b":2}` and `{"b":2,"a":1}` are
identical objects but different strings. So JSON uses **structural** similarity:

```
similarity = mean(key_set_overlap, value_equality_ratio)
```

computed over the flattened JSON paths. Key-set overlap catches dropped or added
fields (the classic schema-migration break); value-equality catches changed values.

### 🔧 Tool calls

```
similarity = name_equality · (0.5 + 0.5 · argument_overlap)
```

Selecting the right tool matters more than the arguments, but both count. A wrong
tool caps the score at `0.5`.

## Worked example

Take a free-text case. `mock:stable` is consistent; `mock:drifty` rewords heavily.

| Quantity | Value |
|---|---:|
| `self_A` (stable, 3 samples) | `0.92` |
| `self_B` (drifty, 3 samples) | `0.67` |
| `cross` | `0.20` |

```
noise = min(0.92, 0.67) = 0.67
drift = max(0, 0.67 − 0.20) / 0.67
      = 0.47 / 0.67
      = 0.701
```

`0.701 ≥ 0.35`, so the `semantic` flag fires and the case is counted as drifted.

Now suppose the same case had `self_B = 0.20` (drifty is very noisy on this
prompt). Then:

```
noise = min(0.92, 0.20) = 0.20
drift = max(0, 0.20 − 0.20) / 0.20 = 0
```

No drift. The difference between the models is no larger than B's own variance —
so we do not claim it.

## What is *not* drift

`drift` is a *semantic* measure. Deterministic, categorical regressions are
detected separately by their own flags, because they are exact rather than
statistical:

- schema validity, strict JSON, refusals, expected hits, tool choice, tool
  arguments, errors, truncation, language, latency, length

A model can drift by `0.0` and still fail the suite — for example, by dropping a
required JSON key every single time, which is deterministic and therefore shows up
as a `schema` flag and a threshold violation rather than as semantic drift.

## Flags

| Flag | Rule |
|---|---|
| `errors` | B's error rate > A's, and > 0 |
| `refusal` | B's refusal rate > A's |
| `schema` | B's schema-valid rate < A's |
| `strict_json` | B's strict-JSON rate < A's |
| `expected` | A hit the expectation on a sample, B never did |
| `expected_tool` | `expect_tool` was called by A but not by B |
| `tool_choice` | B called a different tool than A |
| `tool_args` | B's tool arguments stopped validating against the tool schema |
| `semantic` | `drift ≥ --drift-threshold`, free-text cases only |
| `length` | output length changed by more than 60% |
| `language` | detected language switched |
| `latency` | B's p50 > 2× A's **and** > +500 ms |
| `truncated` | `finish_reason=length` appears only in B |

Each rule is printed into `report.json` under `summary.flag_rules`, so a report
carries its own explanation.
