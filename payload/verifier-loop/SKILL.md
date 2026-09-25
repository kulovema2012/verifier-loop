---
name: verifier-loop
description: Engineer a deterministic verifier for a hard-to-check goal, then run an autonomous agent loop against it until a measurable target is met. Use whenever the user keeps re-prompting an agent to "fix it again", is eyeballing outputs to judge quality (visual fidelity, import/export accuracy, rendering, conversions, migrations, performance, flakiness, error rates), wants an agent to "grind", "run overnight", "not stop until it's right", or asks how to make something verifiable, set up /goal, a Ralph loop, a pixel diff, golden-file tests, a benchmark gate, or a long-running agent task. Also use when the user says they are the bottleneck or "the human in the loop" for checking agent output, even if they never say "verifier".
---

# Verifier Loop

Turn "I'll know it when I see it" into a number an agent can drive down, then let the agent grind on that number for hours or days without you.

## The core idea

When an agent can check its own progress against a clear, deterministic signal, it can loop until the job is truly done. When it can't, a human ends up as the verifier: look at output, spot a problem, re-prompt, repeat. That is the worst possible place for a human in the loop, because it is slow, inconsistent, and caps the work at the human's patience.

So the leverage in agentic engineering has moved. Writing the code is increasingly the agent's job. **Building the verifier is the engineer's job**, and it is where creativity and experience pay off. Examples of what this unlocks:

- A text-layout library brought to pixel-perfect parity with real browser rendering by agents comparing their output to the browser for weeks.
- Figma / Google Slides / PowerPoint import-export fidelity driven from 20-50% pixel mismatch to under 1% over one unattended weekend, by rendering original vs. round-tripped files and pixel-diffing them across a large corpus of community files.

Neither problem looks unit-testable at first ("does this look right?"). Both became tractable the moment someone found a deterministic proxy for "right".

A second consequence: **with a deterministic verifier you often don't need the smartest model.** The number tells the model at every step whether it is winning, so a cheaper, faster model at high effort can grind for days on a budget. Save frontier models for designing the verifier and for reviewing the final result.

## Workflow

Work through these phases in order. Phases 1-4 are where the value is; do not rush to launching the loop. A loop with a weak verifier just produces confident garbage faster.

### Phase 1 — Frame the goal and find the human verifier

Pin down with the user:

1. **What does "done" look like?** Get concrete examples of good and bad output. If they have been re-prompting, ask what they kept complaining about; each complaint is a candidate check.
2. **Who or what is currently judging success?** If the answer is "me, by looking at it", that is the thing to replace.
3. **What is the unit of work?** One file? One page? One request? This becomes a test case.
4. **What is out of scope / irreducible?** E.g. font rasterization differences between machines, timestamps, random IDs. Name these now so they can be masked, not "fixed".
5. **Budget and stopping:** how long may it run, what model/plan, and what score is good enough.

If the user's request is already clear (they named the metric and the target), don't interrogate; confirm your understanding in one or two lines and move on.

### Phase 2 — Choose a verifier

Find a signal that is **deterministic, scalar (or reducible to one), cheap to run, and correlated with what the human actually cares about.** Read `references/verifier-catalog.md` for the full menu with pitfalls; the most useful patterns:

| Situation | Verifier pattern |
|---|---|
| Output is visual (render, slide, design, chart, PDF) | Render both → **pixel diff** % (`scripts/pixel_diff.py`) |
| Converter / importer / exporter / serializer | **Round-trip**: A → B → A', diff A vs A' (visual or structural) |
| Reimplementing something that already exists | **Differential / oracle**: compare to the reference implementation (real browser, old system, upstream lib) on the same inputs |
| Performance | **Percentile latency / throughput** vs. a target (`scripts/bench.py`) |
| Reliability | Error rate or flake rate over N repeated runs |
| Behavior / UX flows | Scripted browser automation with pass/fail per flow |
| Correctness of logic | Unit / property-based tests, type checker, compiler warnings count |
| Genuinely subjective quality | LLM-as-judge with a fixed rubric + anchor examples — a last resort; see catalog |

Composite scores are fine (e.g. mean pixel diff across cases, with a hard floor on every case), but keep the headline one number the agent can optimize.

When part of the target *cannot* match exactly, e.g. mirroring a design whose photos must be regenerated rather than copied, strict pixel diff plateaus on content that is out of reach, and the agent wastes iterations there. Split the score by scale and by region instead: a **tone** diff after a heavy blur (`pixel_diff.py --blur 6`) for layout, colour and lighting, plus a **detail** diff at light blur for text, icons and edges, reported per named region (panel, list, header…). The agent then knows which gaps are fixable, and the final report can say honestly which remaining differences are irreducible. Hold out a metric the loop never optimizes, such as SSIM, to check that the gains are real.

Explain the choice to the user in a few sentences: why this signal tracks what they care about, and where it could diverge.

### Phase 3 — Build the harness

The harness is a single command the agent runs each iteration. Put it in its own directory (e.g. `verify/`) so it can be protected. Requirements:

- **Two folders.** `verify/` holds the harness and reference data and gets locked. `loop/` holds the contract, notes and final report, which the looping agent must be able to write, so keep it outside anything protected.
- **One command, no arguments needed**, e.g. `python verify/run.py`.
- **Prints one JSON object to stdout** as its last line:
  ```json
  {"score": 3.41, "cases": {"deck-01": 0.8, "deck-02": 12.9}, "artifacts": "verify/out"}
  ```
  `score` is the headline metric. `cases` gives per-case scores so the agent knows *where* to look. `artifacts` points to diff images, logs, or failing outputs the agent can inspect; a heatmap of wrong pixels is far more actionable than a number alone.
- **Deterministic.** Pin fonts, viewport sizes, DPI, random seeds, timezones, locales. Run it twice on unchanged code; if the score moves, fix that before anything else. Measure the noise floor — improvements smaller than the noise are not improvements.
- **Diverse corpus.** A verifier that passes on three hand-picked samples is a trap. Gather many real-world inputs (community files, production samples, varied sizes and features). Ask the agent to go find a wide variety of examples if the user has none. Keep a **held-out** subset the loop never sees during iteration, and score it only at checkpoints and at the end, to catch overfitting.
- **Masks for irreducible noise.** Handle known-acceptable differences explicitly (ignore regions, tolerance thresholds, normalization), and document them, so the agent doesn't burn a day chasing font hinting.
- **Fast enough to run hundreds of times.** If one run takes 20 minutes, add a quick mode that samples a subset and reserve the full run for checkpoints.

Bundled helpers (run with `--help` for options):

- `scripts/pixel_diff.py` — compares two images or two directories of matching images; writes a red heatmap per pair and prints JSON with % differing pixels. Supports per-channel tolerance and ignore-region masks.
- `scripts/bench.py` — runs a command N times (with warmup) and prints p50/p90/p99/mean latency as JSON.
- `scripts/ledger.py` — the loop's scorekeeper. See Phase 4.

Then **run a baseline** and show the user the starting numbers and a few of the worst cases. This is often the moment the user realizes how bad things were — and it validates that the verifier catches real problems.

### Phase 4 — Harden the verifier against gaming

A loop optimizes whatever you measure, including loopholes. Before launching, think adversarially about how an agent could make the number go down without making the product better, and close those paths:

- **Protect the verifier.** The agent must not edit the harness, the corpus, the masks, or the thresholds. `scripts/ledger.py init --protect verify/ corpus/` hashes those paths; every `ledger.py run` refuses to record a score if they changed. Finish and dry-run the harness *before* locking, so you don't need to touch it again. Once a baseline is recorded, `ledger.py relock` is a human decision: the looping agent stops and asks instead of relocking itself, even for a "harmless" fix, because a verifier that its optimizer can edit no longer measures anything. Every post-baseline relock is printed on each run so reviewers see it.
- **Don't leak the answer through derived data.** Copying the reference in a transformed form is still copying: a blurred colour map, a per-pixel lighting/gain map, a low-resolution tracing, or edge maps lifted from the reference. Fitting a *handful of global parameters* against the reference (overall scale, offset, a per-channel linear colour grade) is fine, and so is passing the reference to an image generator as a style guide. When unsure, ask: "would this output still look right if the reference were deleted after fitting?" and "how many numbers did I take from it?"
- **Watch for degenerate solutions.** E.g. an exporter that embeds a screenshot of the original instead of real editable shapes gets a perfect pixel diff. Add a guard check (structure counts, file-type checks, "output must contain editable text") alongside the main metric.
- **No special-casing.** Code that detects specific test inputs by filename or hash is cheating. Say so explicitly in the loop contract and grep for it at review time.
- **Held-out set** as above.

Initialize the ledger:

```bash
python <skill-dir>/scripts/ledger.py init \
  --cmd "python verify/run.py" \
  --direction min --target 1.0 \
  --case-ceiling 5.0 \
  --protect verify corpus \
  --plateau 8
```

Each iteration the agent runs `python <skill-dir>/scripts/ledger.py run`. It executes the harness, checks integrity, compares to the best run so far, lists per-case improvements and regressions, and exits with a code the agent can act on: `0` target met, `1` keep going, `2` plateau (stop and rethink), `3` integrity violation or harness error. `ledger.py status` prints the history.

### Phase 5 — Write the loop contract and launch

Write a loop contract (`loop/LOOP.md`) from `references/loop-contract-template.md`. It tells the looping agent: the goal, the one command to run, the target, what not to touch, how to use artifacts, commit hygiene (commit each improvement so regressions are one `git revert` away), and when to stop.

Then launch using whatever the environment supports, in order of preference:

1. **`/goal`** (if the harness has it): pass the contract as the goal. Long-horizon goal mode is built for exactly this.
2. **A plain prompt** that says: read `loop/LOOP.md`, iterate, and do not stop until `ledger.py run` exits 0 or 2. No plugin is required; the verifier is what makes the loop work.
3. **Fresh-context loop** (e.g. the `ralph-wiggum` skill or a bash loop re-invoking the CLI) for multi-day runs where a single context would degrade. The ledger file carries state between iterations.

If the user wants to launch it themselves, hand them the exact command/prompt instead.

Model guidance for the loop: a cheaper model on max reasoning effort is usually the best cost/benefit once the verifier is solid. Keep the strongest model for Phases 1-4 and Phase 6.

### Phase 6 — Review the result

When the loop stops, don't take the number on faith:

1. Run `ledger.py run` yourself, then score the held-out set.
2. Skim the diff for gaming: special-cased inputs, disabled checks, edited tests, suspicious `try/except: pass`.
3. Look at a handful of best and worst cases with human eyes. The metric is a proxy; make sure it still means what you think.
4. Report to the user: start vs. end score, per-case table of the worst remaining cases, what the remaining gap consists of (often the irreducible noise named in Phase 1), and anything you spotted in review.
5. Suggest turning the harness into a permanent regression gate (CI job) so the gains stick and new edge cases become new corpus entries.

## Deliverables checklist

By the end of a session using this skill, the user should have:

- [ ] A written statement of the goal and the chosen metric, with its known blind spots
- [ ] `verify/` harness: one command → JSON score + per-case breakdown + artifacts
- [ ] A diverse corpus with a held-out split
- [ ] A measured baseline and noise floor
- [ ] An initialized ledger with the verifier protected
- [ ] `loop/LOOP.md` loop contract (with `loop/NOTES.md` and later `loop/REPORT.md`, all outside the locked folder)
- [ ] The loop launched (or the exact launch command handed over)
- [ ] After the run: an independent review and a report

Scale this to the task. For a small job (e.g. "make this one SVG match this PNG") a lightweight harness and a short contract are enough; don't build infrastructure the task doesn't need. The non-negotiables at every scale are a deterministic signal, a protected verifier, and a stopping rule.
