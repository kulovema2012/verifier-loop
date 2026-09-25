# Loop Contract Template

Copy to `loop/LOOP.md` and fill in the brackets. This file is what the looping agent reads at the start of every iteration, so it must be self-contained: assume the reader has no memory of the conversation that produced it.

---

```markdown
# Loop contract: [short goal name]

## Goal
[One or two sentences on the outcome in user terms. e.g. "Slides imported from .pptx must look
identical to how PowerPoint renders them."]

## How progress is measured
Run exactly this, every iteration:

    python [path/to/skill]/scripts/ledger.py run

It runs the verifier (`[harness command]`), records the result, and prints:
- the headline score ([metric name], lower is better | higher is better)
- per-case scores, plus which cases improved or regressed vs. the best run so far
- where the artifacts are ([what they are, e.g. red heatmaps of wrong pixels in verify/out/])

Exit codes: 0 = target met, stop. 1 = keep going. 2 = plateau, stop and write up.
3 = verifier integrity violation or harness error, stop and report.

Target: [score threshold], and no single case worse than [case ceiling].
Starting baseline: [score] ([date]).
Noise floor: about [±x]. Changes smaller than that are not real.

## How to work each iteration
1. Run the ledger. Read the worst cases and open their artifacts. Look before you change code.
2. Form a hypothesis about the biggest remaining cause of error that affects many cases.
   Prefer general fixes over case-by-case patches.
3. Make the change. Re-run the ledger.
4. If the score improved beyond noise and no case regressed past the ceiling, commit with a
   message naming the fix and the new score. Otherwise revert and try another hypothesis.
5. Append one line to loop/NOTES.md: hypothesis, result, score. This prevents retrying
   dead ends after a context reset.

## Rules (the verifier is off-limits)
- Do not modify anything under: [protected paths, e.g. verify/, corpus/]. The ledger detects
  this and refuses to record scores. If you believe the verifier itself is wrong, stop and
  explain why instead of changing it.
- Do not special-case inputs by filename, content hash, or any other way of recognizing
  specific test cases. Fixes must be general.
- Do not [degenerate shortcut specific to this task, e.g. "embed rasterized images of the
  source in the output"]. Output must [structural guard, e.g. "keep text as editable text"].
- Known irreducible differences, which you should not chase: [e.g. "font anti-aliasing,
  sub-pixel kerning differences between renderers"].

## When to stop
- Ledger exits 0 (target met): run the held-out check `[held-out command]`, then write the report.
- Ledger exits 2 (plateau): write the report describing what remains and what you tried.
- Ledger exits 3: stop immediately and report.
- Budget: [time / iteration limit, if any].

## Report (write to loop/REPORT.md)
Start score → end score, held-out score, the 5 worst remaining cases with a one-line
explanation each, the main fixes made (with commits), and any doubts about the verifier.
```

---

## Launch prompt

For a harness with `/goal`, pass: "Follow loop/LOOP.md until it says to stop."

For a plain prompt:

> Read loop/LOOP.md and follow it. Keep iterating: do not stop or ask me questions until the ledger exits 0, 2, or 3. When you stop, write loop/REPORT.md.

For a fresh-context bash loop (each iteration a new agent process), the ledger file and loop/NOTES.md carry the state, so the same prompt works every iteration; exit the outer loop when `ledger.py check` returns non-1.
