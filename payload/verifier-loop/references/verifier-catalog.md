# Verifier Catalog

A menu of ways to turn a fuzzy goal into a deterministic signal. For each: when it fits, how to build it, and how it breaks.

## Contents

1. Pixel diff (visual parity)
2. Round-trip fidelity
3. Differential / oracle testing
4. Structural diff (DOM, AST, JSON, XML)
5. Golden files / snapshots
6. Performance percentiles
7. Error rate and flake rate
8. Browser-automated flows
9. Property-based and fuzz testing
10. Static signals (types, lints, warnings, bundle size)
11. LLM-as-judge (last resort)
12. Combining signals

---

## 1. Pixel diff (visual parity)

**Fits:** anything whose correctness is "does it look the same": renderers, design/slide import-export, PDF generation, charts, CSS refactors, canvas/WebGL, email templates.

**Build:** render expected and actual to PNG at identical size/DPI, then `scripts/pixel_diff.py expected.png actual.png --heatmap out/diff.png`. For a corpus, pass two directories with matching filenames. Report % of differing pixels per case; headline = mean (or p90) across cases.

**Pitfalls:**
- Anti-aliasing and font hinting produce 0.1-1% noise even when everything is right. Use `--tolerance` (per-channel delta, e.g. 16-32) and measure the floor on a known-identical pair first.
- Different machines render fonts differently. Pin fonts (bundle them), pin the browser version, run in the same container.
- Size mismatch is itself a bug; the script counts non-overlapping area as fully different rather than resizing, so it can't be hidden.
- Pixel % treats a 1px shift of a big block as a huge error while a missing small icon barely registers. Consider pairing with a per-region or perceptual check (SSIM) when small elements matter.
- Degenerate win: rasterizing the source into the output. Guard with a structural check.

**Mirroring a design with regenerated imagery** (rebuilding a mockup as HTML/CSS while the photos must be generated, not cropped from the reference):
- Score two passes: tone (`--blur 6`, tolerance ~12) for layout, glass tints and lighting; detail (`--blur 1`, tolerance ~32) for text, icons and edges. Average them for the headline and report both per named region.
- Guard in the harness: fail the run if the page references the reference file, if required UI strings aren't real DOM text (catches a flattened screenshot), or if any placed image correlates > 0.95 with the reference pixels under its on-screen box (catches pasted crops; get boxes with `getBoundingClientRect` via Playwright).
- Fitting a generated background to the reference with a few global parameters (scale, offset, a linear per-channel grade) is fair. Spatial maps fitted from the reference (blurred gain maps, per-region grading) copy it in disguise.
- Pixel metrics reward *less ink*: an automated tuner will shrink font sizes, thin strokes and fade icons, because there are fewer pixels to get wrong. Lock typography first, by matching measured text-run widths per label to the reference, then keep font size, weight and text colour out of any automated tuning. A per-label text-extent check in the guard makes this enforceable.
- Expect a floor: faces and photos won't match. Use a held-out SSIM, which the loop never optimizes, to confirm the gains are real.

## 2. Round-trip fidelity

**Fits:** importers, exporters, converters, serializers, migrations, codecs.

**Build:** A → (import) → internal → (export) → A'. Compare A and A' with the most appropriate diff (pixel for visual formats, structural for data). Also test one-way: A → internal, rendered, vs. A rendered by its native tool (that's differential, see 3).

**Pitfalls:** a lossy-but-symmetric pipeline can round-trip perfectly while the internal representation is wrong (e.g. storing the whole original blob and re-emitting it). Check the internal form too, or render the internal form directly.

## 3. Differential / oracle testing

**Fits:** reimplementing, porting, or optimizing something that already exists: a layout engine vs. the browser, a new parser vs. the old one, Rust port vs. Python original, an optimized query vs. the naive one.

**Build:** feed identical inputs to both; diff outputs. The reference is the oracle. Generate inputs broadly (corpus + random generation).

**Pitfalls:** the oracle has bugs too. When they disagree on a case the user would call the oracle wrong, add it to an explicit exceptions list with a reason, rather than letting the agent reproduce the bug. The exceptions list is part of the protected verifier.

## 4. Structural diff (DOM, AST, JSON, XML)

**Fits:** data transforms, code generators, API responses, document models.

**Build:** normalize both sides (sort keys, strip volatile fields like IDs and timestamps, canonical whitespace) and count differing nodes/fields. Tree-edit distance or a simple "fraction of leaf paths that match" both work as scores.

**Pitfalls:** over-normalization hides real bugs. Normalize only what is genuinely irrelevant, and list each normalization in the harness README.

## 5. Golden files / snapshots

**Fits:** CLIs, compilers, formatters, report generators — anything with stable textual output.

**Build:** a directory of input → expected-output pairs; score = number (or %) of mismatching cases, with a line-diff artifact per failure.

**Pitfalls:** the classic cheat is regenerating the goldens. Protect the golden directory with the ledger. Goldens produced by the buggy code being fixed are wrong; have a human bless the goldens or derive them from an oracle.

## 6. Performance percentiles

**Fits:** latency, throughput, memory, startup time, build time.

**Build:** `scripts/bench.py --runs 50 --warmup 5 -- <command>` prints p50/p90/p99. For services, use a load generator and read p90 from its report. Optimize p90 or p99, not the mean, since tails are what users feel.

**Pitfalls:** noisy machines. Run on a quiet host, repeat, and only count improvements larger than the observed run-to-run spread. Pair with a correctness check, because deleting work is the fastest optimization. Watch for caching that only helps the benchmark.

## 7. Error rate and flake rate

**Fits:** reliability work, flaky tests, crash reduction, "it sometimes fails".

**Build:** run the flow N times (N large enough that the target rate is measurable: to claim < 1% you need hundreds of runs); score = failures / N. For production error rates, replay recorded traffic in staging.

**Pitfalls:** retries that mask failures, catch-all error swallowing. Count swallowed exceptions too, or grep for them during review.

## 8. Browser-automated flows

**Fits:** UX flows, signup/checkout, cross-browser behavior, accessibility.

**Build:** Playwright (or similar) scripts, one per flow, each asserting on visible outcomes. Score = failing flows; artifacts = screenshots and traces of failures. Add axe-core for an accessibility-violation count.

**Pitfalls:** selectors the agent can "fix" by changing test IDs. Protect the test scripts; assert on user-visible text and roles, not implementation details.

## 9. Property-based and fuzz testing

**Fits:** parsers, serializers, math, state machines, anything with invariants ("decode(encode(x)) == x", "output is sorted", "never panics").

**Build:** Hypothesis / fast-check / cargo-fuzz with a fixed seed and example budget for determinism in the loop; score = failing properties or unique crashes.

**Pitfalls:** a fixed seed can overfit; rotate the seed at checkpoints and on the held-out pass.

## 10. Static signals

**Fits:** migrations and cleanups: type errors remaining, lint violations, deprecated API call sites, bundle size, dependency count.

**Build:** run the tool, count, emit JSON. These are cheap and great as guard metrics alongside a primary one.

**Pitfalls:** suppression comments (`# type: ignore`, `eslint-disable`, `@ts-expect-error`). Count those too, and treat any increase as a regression.

## 11. LLM-as-judge (last resort)

**Fits:** genuinely subjective quality where no mechanical proxy exists: tone, clarity of generated docs, summary faithfulness.

**Build:** a fixed rubric with explicit criteria scored 1-5 each, a few anchor examples of each score level included in the judge prompt, temperature 0, a pinned judge model, and multiple judge samples averaged. Score is the rubric total. Keep a set of human-labeled cases and check the judge agrees with them before trusting it.

**Pitfalls:** not deterministic; judges are gameable (verbosity, flattery, keyword stuffing); the looping model and the judge may share blind spots. Use a different model family for judging if possible. Always try harder to find a mechanical proxy first; often part of the goal is mechanical (length, required sections, factual claims checkable against a source) and only a residue needs a judge.

## 12. Combining signals

Real goals usually need one **primary** metric the loop optimizes and a few **guards** that must not regress:

- Primary: mean pixel diff across the corpus.
- Guards: every case ≤ 5% (case ceiling), output file contains editable text nodes, no increase in suppression comments, harness runtime < 2 min.

Encode guards in the harness: if any guard fails, either report it as a hard failure (non-JSON exit or a `"guards_failed": [...]` field that the harness turns into a very bad score). Keep the headline a single number; the agent optimizes more reliably against one number with hard constraints than against a weighted blend it can trade off.
