# Cost control

A 300-case suite × 3 samples × 2 models is 1,800 calls. Know the bill before you
run it.

## See the projection first

```console
$ modelbump diff --from gpt-4.1 --to gpt-5 --suite suite.jsonl --dry-run
```

`--dry-run` estimates token counts from the inputs and prints the projected cost
**without making a single call**. Use it whenever you are about to try a new,
large suite.

## Hard budget

```console
$ modelbump diff --from gpt-4.1 --to gpt-5 --suite suite.jsonl --max-cost 5.00
```

If the projection exceeds `--max-cost`, the run **aborts before spending anything**
and exits `2`:

```text
🛑 budget exceeded: projected $8.40 > max $5.00
```

The guard is checked up front, so it cannot be surprised by a runaway suite.

## Pricing

Cost comes from the `surfacelock` registry, including **cached-input pricing** —
a cache hit is cheaper than a fresh call, and the report reflects that.

| Situation | Reported cost |
|---|---|
| Known model | Computed from tokens in/out/cached |
| Unknown model | `null` — **never a misleading zero** |

A `null` cost means "we do not know", which is different from "it was free". The
report keeps that distinction so a cost comparison across an unknown model is
visibly incomplete rather than quietly wrong.

## The cache is the biggest lever

Every response is cached on disk at `.modelbump/cache/`, keyed by:

```
(provider, model, base_url, case fingerprint, params, sample)
```

So a re-run costs nothing:

```console
$ modelbump diff ... --suite suite.jsonl
   cache: 0 hits / 108 misses

$ modelbump diff ... --suite suite.jsonl
   cache: 108 hits / 0 misses · $0.00
```

| Flag | |
|---|---|
| `--no-cache` | Bypass the cache entirely |
| `--refresh` | Re-run and overwrite cached entries |
| `modelbump cache stats` | Entries and size on disk |
| `modelbump cache clear` | Delete the cache |

**Errors are not cached.** A transient 500 does not get pinned; the next run
retries it.

## Resume

Interrupted runs resume from the cache. Kill a run halfway, restart the same
command, and it picks up where it stopped — only the missing samples are fetched.

## Judge cost

Judge calls count toward both the report's cost totals and `--max-cost`. The judge
runs only on flagged cases by default, so a clean migration costs nothing extra.

## Tips

- Use `--limit N` to try a change on the first N cases before committing to a full run.
- Use `--tag` to diff only the surface you care about while iterating.
- Run the `mock:` variants to validate your CI wiring for free.
- Keep `report.json`; `modelbump report` re-renders without any model calls.
