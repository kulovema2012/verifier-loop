# verifier-loop

An agent skill for **Claude Code** and **Codex**. It turns "I'll know it when I see it" into a number an agent can drive down, then lets the agent loop on that number until the target is met, so you stop being the human who re-checks every output.

The idea: when an agent can check its own progress against a clear, deterministic signal, it can keep working for hours without you. Writing the code is increasingly the agent's job. Building the verifier is the engineer's.

## Install

```bash
npx verifier-loop install            # both tools, user scope (~/.claude/skills and ~/.agents/skills)
npx verifier-loop install --only claude
npx verifier-loop install --scope project --project ./my-repo
npx verifier-loop verify             # check the installed copies match the package
npx verifier-loop uninstall          # removes the skill, keeping a backup
```

Add `--dry-run` to see what would change. An existing copy is backed up to `~/.verifier-loop-backups/` before it is replaced.

The bundled scripts need Python 3. `pixel_diff.py` also needs `pip install pillow numpy`.

## What the skill does

It triggers when you keep re-prompting an agent to "fix it again", eyeball outputs to judge quality, or ask an agent to grind, run overnight, or not stop until something is right. It then works in six phases:

1. **Frame the goal**: what "done" means, and who is currently judging it by eye.
2. **Choose a verifier**: pixel diff, round-trip, differential/oracle testing, structural diff, golden files, latency percentiles, flake rate, browser flows, property tests, static counts, or an LLM judge as a last resort (`references/verifier-catalog.md`).
3. **Build the harness**: one command that prints JSON with a score, per-case scores and artifacts. It must be deterministic, cover a diverse corpus, and keep a held-out set.
4. **Harden it against gaming**: lock the verifier, block degenerate solutions, and forbid special-casing or copying the reference in disguise.
5. **Write the loop contract and launch**: via `/goal`, a plain prompt, or a fresh-context (Ralph) loop.
6. **Review the result**: re-score, check the held-out set, read the diff for cheating, and look at real outputs.

## Bundled scripts

| Script | What it does |
|---|---|
| `scripts/ledger.py` | Scorekeeper. It runs the harness and refuses to record a score if the locked verifier files (or its own config) changed. It tracks the best score, per-case regressions and plateaus, and warns about any re-lock after the baseline. Exit codes: `0` target met, `1` keep going, `2` plateau, `3` tampered or broken. |
| `scripts/pixel_diff.py` | Diffs two images or directories. Writes red heatmaps and prints harness JSON. Supports tolerance, ignore regions, masks and `--blur` for tone-only comparisons. |
| `scripts/bench.py` | Runs a command N times and reports p50/p90/p99 latency as harness JSON. A failing run voids the score. |

## Tested

The skill was tested against a no-skill baseline on four tasks: a buggy renderer driven to pixel parity, a latency harness with an oracle correctness guard, a Python-to-TypeScript differential port plan, and rebuilding a visionOS design mockup as HTML/CSS judged by an independent grader. With the skill, the agent passed every check (30/30). Without it, it passed 89.7%. The misses were an unprotected verifier, a missing loop contract, and background lighting copied from the reference.

## License

MIT
