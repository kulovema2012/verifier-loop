#!/usr/bin/env python3
"""Scorekeeper for a verifier loop: runs the harness, guards the verifier, tracks progress.

Subcommands
    init     configure the loop and snapshot (hash) the protected verifier files
    run      verify integrity, run the harness, record the score, print a verdict
    check    exit with the verdict of the latest run without running anything
    status   print the run history
    relock   re-snapshot protected files after a HUMAN-approved verifier change

Exit codes (run / check)
    0  target met: score meets --target and no case is past --case-ceiling
    1  keep going
    2  plateau: no improvement larger than --min-delta for --plateau consecutive runs
    3  integrity violation (protected files changed) or harness error / unparseable output

The harness must print a JSON object as the last JSON line of stdout:
    {"score": 3.41, "cases": {"name": 0.8, ...}, "artifacts": "verify/out"}
Only "score" is required. A null score counts as a harness error.
The command runs without a shell; if it needs pipes, `&&` or env setup, put that in the
harness script itself.

State lives in a JSON file (default .verifier-ledger.json in the current directory).

Example
    ledger.py init --cmd "python verify/run.py" --direction min --target 1.0 \\
        --case-ceiling 5 --protect verify corpus --exclude verify/out --plateau 8
    ledger.py run
"""
import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

DEFAULT_LEDGER = ".verifier-ledger.json"
ALWAYS_EXCLUDE = ["**/__pycache__/**", "**/*.pyc", "**/.DS_Store", "**/node_modules/**"]
EXIT_DONE, EXIT_CONTINUE, EXIT_PLATEAU, EXIT_BROKEN = 0, 1, 2, 3


# ---------- helpers ----------

def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def norm(p):
    return Path(p).as_posix().rstrip("/")


def is_excluded(rel, excludes):
    for pat in excludes + ALWAYS_EXCLUDE:
        pat = norm(pat)
        if fnmatch.fnmatch(rel, pat) or rel == pat or rel.startswith(pat + "/"):
            return True
    return False


def file_digest(path):
    """sha256 of a file. Text files are hashed with CRLF normalised to LF, so git's
    core.autocrlf rewriting line endings on checkout is not mistaken for tampering."""
    data = Path(path).read_bytes()
    if b"\0" not in data[:8192]:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def hash_tree(paths, excludes, ledger_path):
    """Return {relative_posix_path: sha256} for every file under the protected paths."""
    out = {}
    ledger_rel = norm(os.path.relpath(ledger_path))
    for root in paths:
        root_p = Path(root)
        if not root_p.exists():
            out[norm(root)] = "MISSING"
            continue
        files = [root_p] if root_p.is_file() else sorted(p for p in root_p.rglob("*") if p.is_file())
        for f in files:
            rel = norm(os.path.relpath(f))
            if rel == ledger_rel or is_excluded(rel, excludes):
                continue
            out[rel] = file_digest(f)
    return out


def diff_hashes(old, new, excludes):
    keep = lambda d: {k: v for k, v in d.items() if not is_excluded(k, excludes)}
    old, new = keep(old), keep(new)
    changed = sorted(k for k in old.keys() & new.keys() if old[k] != new[k])
    added = sorted(new.keys() - old.keys())
    removed = sorted(old.keys() - new.keys())
    return changed, added, removed


def split_cmd(cmd):
    """Harness command -> argv. No shell: put pipelines or env setup inside the harness script."""
    if os.name == "nt":
        return cmd  # CreateProcess parses the string itself; posix shlex would mangle backslashes
    return shlex.split(cmd)


def fingerprint(cfg, hashes):
    """Tamper-evidence for the ledger itself. Not cryptographic security: a determined agent
    could recompute it, but it stops casual edits to the harness command or target."""
    blob = json.dumps({"config": cfg, "hashes": hashes}, sort_keys=True).encode()
    return hashlib.sha256(b"verifier-loop:" + blob).hexdigest()


def load(path):
    p = Path(path)
    if not p.exists():
        sys.exit(f"no ledger at {p}; run `ledger.py init` first")
    return json.loads(p.read_text(encoding="utf-8"))


def save(path, state):
    Path(path).write_text(json.dumps(state, indent=2), encoding="utf-8")


def git_rev():
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=10)
        if rev.returncode == 0:
            return rev.stdout.strip() + ("+dirty" if dirty.stdout.strip() else "")
    except Exception:
        pass
    return None


def better(a, b, direction, min_delta=0.0):
    """True if score a beats score b by more than min_delta."""
    if b is None:
        return True
    return (a < b - min_delta) if direction == "min" else (a > b + min_delta)


def meets(value, limit, direction):
    return value <= limit if direction == "min" else value >= limit


def fmt(x):
    return "n/a" if x is None else (f"{x:.4g}" if isinstance(x, float) else str(x))


def parse_harness_output(stdout):
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "score" in obj:
                return obj
    # whole stdout might be a pretty-printed JSON object
    try:
        obj = json.loads(stdout)
        if isinstance(obj, dict) and "score" in obj:
            return obj
    except json.JSONDecodeError:
        pass
    return None


def verdict(state, run):
    cfg = state["config"]
    d = cfg["direction"]
    if run.get("error"):
        return EXIT_BROKEN
    over = [k for k, v in (run.get("cases") or {}).items()
            if cfg.get("case_ceiling") is not None and isinstance(v, (int, float)) and not meets(v, cfg["case_ceiling"], d)]
    if cfg.get("target") is not None and meets(run["score"], cfg["target"], d) and not over:
        return EXIT_DONE
    if cfg.get("plateau") and state.get("stale_runs", 0) >= cfg["plateau"]:
        return EXIT_PLATEAU
    return EXIT_CONTINUE


LABEL = {EXIT_DONE: "TARGET MET - stop", EXIT_CONTINUE: "KEEP GOING",
         EXIT_PLATEAU: "PLATEAU - stop and write up", EXIT_BROKEN: "BROKEN - stop and report"}


# ---------- commands ----------

def cmd_init(a):
    if Path(a.ledger).exists() and not a.force:
        sys.exit(f"{a.ledger} exists; pass --force to overwrite (this discards run history)")
    excludes = [norm(e) for e in a.exclude]
    state = {
        "config": {
            "cmd": a.cmd, "direction": a.direction, "target": a.target, "case_ceiling": a.case_ceiling,
            "protect": [norm(p) for p in a.protect], "exclude": excludes,
            "plateau": a.plateau, "min_delta": a.min_delta, "timeout": a.timeout,
        },
        "locked_at": now(),
        "hashes": hash_tree(a.protect, excludes, a.ledger),
        "runs": [], "best": None, "stale_runs": 0,
    }
    state["config_fingerprint"] = fingerprint(state["config"], state["hashes"])
    save(a.ledger, state)
    missing = [k for k, v in state["hashes"].items() if v == "MISSING"]
    print(f"ledger initialised at {a.ledger}")
    print(f"  harness:   {a.cmd}")
    print(f"  goal:      {a.direction}imize score, target {fmt(a.target)}, per-case ceiling {fmt(a.case_ceiling)}")
    print(f"  protected: {len(state['hashes']) - len(missing)} files under {', '.join(a.protect) or '(nothing)'}")
    if missing:
        print(f"  WARNING: protected path(s) do not exist yet: {', '.join(missing)}")
    if not a.protect:
        print("  WARNING: nothing protected - the looping agent could edit the verifier")
    print("next: `ledger.py run` to record the baseline")


def cmd_run(a):
    state = load(a.ledger)
    cfg = state["config"]
    d = cfg["direction"]
    run = {"n": len(state["runs"]) + 1, "at": now(), "git": git_rev()}

    # 1. integrity: verifier files, and the ledger's own config (harness cmd, target, ...)
    current = hash_tree(cfg["protect"], cfg["exclude"], a.ledger)
    changed, added, removed = diff_hashes(state["hashes"], current, cfg["exclude"])
    if state.get("config_fingerprint") and state["config_fingerprint"] != fingerprint(cfg, state["hashes"]):
        changed = changed + [f"{a.ledger} (config or lock edited by hand)"]
    if changed or added or removed:
        run["error"] = "integrity"
        print("INTEGRITY VIOLATION: protected verifier files changed since lock.")
        for label, items in (("changed", changed), ("added", added), ("removed", removed)):
            for i in items[:20]:
                print(f"  {label}: {i}")
        print("The score was NOT recorded. Revert those changes (e.g. `git checkout -- <path>`).")
        print("If a human approved a verifier change, they can run `ledger.py relock`.")
        state["runs"].append({**run, "score": None, "changed": changed + added + removed})
        save(a.ledger, state)
        print(f"\nverdict: {LABEL[EXIT_BROKEN]} (exit 3)")
        return EXIT_BROKEN

    # 2. harness
    try:
        proc = subprocess.run(split_cmd(cfg["cmd"]), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=cfg.get("timeout"))
        stdout, stderr, rc = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        stdout, stderr, rc = "", f"harness timed out after {cfg.get('timeout')}s", -1
    except OSError as e:
        stdout, stderr, rc = "", f"could not start harness: {e}", -1

    result = parse_harness_output(stdout)
    if result is None or result.get("score") is None:
        run["error"] = "harness"
        print(f"HARNESS ERROR: no JSON with a numeric score on stdout (exit code {rc}).")
        tail = (stderr or stdout).strip().splitlines()[-15:]
        for line in tail:
            print(f"  | {line}")
        state["runs"].append({**run, "score": None, "rc": rc})
        save(a.ledger, state)
        print(f"\nverdict: {LABEL[EXIT_BROKEN]} (exit 3)")
        return EXIT_BROKEN

    run.update(score=float(result["score"]), cases=result.get("cases") or {},
               artifacts=result.get("artifacts"), rc=rc)
    extra = {k: v for k, v in result.items() if k not in ("score", "cases", "artifacts")}
    if extra:
        run["extra"] = extra

    # 3. compare with best
    best = state["best"]
    best_run = next((r for r in state["runs"] if r["n"] == best), None) if best else None
    best_score = best_run["score"] if best_run else None
    improved = better(run["score"], best_score, d, cfg.get("min_delta") or 0.0)
    if improved:
        state["best"] = run["n"]
        state["stale_runs"] = 0
    else:
        state["stale_runs"] = state.get("stale_runs", 0) + 1
    state["runs"].append(run)
    save(a.ledger, state)

    # 4. report
    baseline = next((r["score"] for r in state["runs"] if r.get("score") is not None), None)
    print(f"run #{run['n']}  score {fmt(run['score'])}   best {fmt(best_score if not improved else run['score'])}"
          f"   baseline {fmt(baseline)}   target {'<=' if d == 'min' else '>='} {fmt(cfg.get('target'))}")
    if improved and best_score is not None:
        print(f"NEW BEST (was {fmt(best_score)} at run #{best})")
    elif not improved:
        print(f"no improvement over best (run #{best}); stale for {state['stale_runs']}"
              f"{'/' + str(cfg['plateau']) if cfg.get('plateau') else ''} run(s)")

    cases = run["cases"]
    if cases:
        numeric = {k: v for k, v in cases.items() if isinstance(v, (int, float))}
        worst = sorted(numeric.items(), key=lambda kv: kv[1], reverse=(d == "min"))[:a.show]
        print(f"worst {len(worst)} of {len(cases)} cases: " + ", ".join(f"{k}={fmt(v)}" for k, v in worst))
        if best_run and best_run.get("cases"):
            prev = best_run["cases"]
            ups = [(k, prev[k], v) for k, v in numeric.items() if isinstance(prev.get(k), (int, float)) and better(v, prev[k], d)]
            downs = [(k, prev[k], v) for k, v in numeric.items() if isinstance(prev.get(k), (int, float)) and better(prev[k], v, d)]
            if ups:
                print(f"improved vs best ({len(ups)}): " + ", ".join(f"{k} {fmt(o)}->{fmt(n)}" for k, o, n in ups[:a.show]))
            if downs:
                print(f"REGRESSED vs best ({len(downs)}): " + ", ".join(f"{k} {fmt(o)}->{fmt(n)}" for k, o, n in downs[:a.show]))
        if cfg.get("case_ceiling") is not None:
            over = [k for k, v in numeric.items() if not meets(v, cfg["case_ceiling"], d)]
            if over:
                print(f"past case ceiling {fmt(cfg['case_ceiling'])} ({len(over)}): {', '.join(over[:a.show])}")
    if run.get("artifacts"):
        print(f"artifacts: {run['artifacts']}")
    if rc not in (0, None):
        print(f"note: harness exited {rc} but produced a score")

    late = late_relocks(state)
    if late:
        print(f"WARNING: verifier was relocked {len(late)} time(s) after the baseline "
              f"({'; '.join(r['reason'] for r in late)}). Relocking is a human decision; scores before "
              f"and after a relock are not comparable.")

    v = verdict(state, run)
    print(f"\nverdict: {LABEL[v]} (exit {v})")
    return v


def late_relocks(state):
    """Relocks made after the first scored run."""
    first = next((r["at"] for r in state["runs"] if r.get("score") is not None), None)
    return [r for r in state.get("relocks", [])
            if r.get("scored_runs_before", 1 if first and r["at"] > first else 0) > 0]


def cmd_check(a):
    state = load(a.ledger)
    if not state["runs"]:
        print("no runs yet")
        return EXIT_CONTINUE
    run = state["runs"][-1]
    v = verdict(state, run)
    print(f"last run #{run['n']} score {fmt(run.get('score'))}: {LABEL[v]}")
    return v


def cmd_status(a):
    state = load(a.ledger)
    cfg = state["config"]
    print(f"harness: {cfg['cmd']}")
    print(f"goal: {cfg['direction']}imize, target {fmt(cfg.get('target'))}, case ceiling {fmt(cfg.get('case_ceiling'))}, "
          f"plateau {fmt(cfg.get('plateau'))}, locked {state['locked_at']}")
    print(f"{'run':>4}  {'when':19}  {'score':>10}  {'git':14}  note")
    for r in state["runs"][-a.last:]:
        note = r.get("error") or ("best" if r["n"] == state["best"] else "")
        print(f"{r['n']:>4}  {r['at']:19}  {fmt(r.get('score')):>10}  {str(r.get('git') or ''):14}  {note}")
    for rl in state.get("relocks", []):
        tag = "AFTER BASELINE" if rl in late_relocks(state) else "setup"
        print(f"relock [{tag}] {rl['at']}: {rl['reason']} ({', '.join(rl['changed'])})")
    return 0


def cmd_relock(a):
    state = load(a.ledger)
    cfg = state["config"]
    new = hash_tree(cfg["protect"], cfg["exclude"], a.ledger)
    changed, added, removed = diff_hashes(state["hashes"], new, cfg["exclude"])
    config_edited = state.get("config_fingerprint") != fingerprint(cfg, state["hashes"])
    if config_edited:
        changed = changed + ["(ledger config)"]
    if not (changed or added or removed):
        print("protected files unchanged; nothing to relock")
        return 0
    if not a.reason:
        sys.exit("relock requires --reason (who approved the verifier change and why)")
    state["hashes"] = new
    state["config_fingerprint"] = fingerprint(cfg, new)
    state["locked_at"] = now()
    scored = sum(1 for r in state["runs"] if r.get("score") is not None)
    state.setdefault("relocks", []).append({"at": now(), "reason": a.reason, "changed": changed + added + removed,
                                            "scored_runs_before": scored})
    if scored:
        print("WARNING: relocking after a baseline exists. This must be a human decision; it will be "
              "reported on every run.")
    # Scores from the old verifier aren't comparable; restart best/plateau tracking.
    state["best"] = None
    state["stale_runs"] = 0
    save(a.ledger, state)
    print(f"relocked {len(changed + added + removed)} changed file(s); best-score tracking reset")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", default=DEFAULT_LEDGER, help="ledger file (default: %(default)s)")
    sub = ap.add_subparsers(dest="command", required=True)

    i = sub.add_parser("init", help="configure and lock the verifier")
    i.add_argument("--cmd", required=True, help="harness command (run through the shell)")
    i.add_argument("--direction", choices=["min", "max"], default="min", help="is lower or higher better")
    i.add_argument("--target", type=float, help="headline score that counts as done")
    i.add_argument("--case-ceiling", type=float,
                   help="worst acceptable per-case score (max for --direction min, min for --direction max)")
    i.add_argument("--protect", nargs="*", default=[], help="files/dirs the loop must not modify")
    i.add_argument("--exclude", nargs="*", default=[], help="paths/globs inside protected dirs that may change (e.g. artifact output)")
    i.add_argument("--plateau", type=int, default=10, help="stop after this many runs without a new best (0 = never)")
    i.add_argument("--min-delta", type=float, default=0.0, help="improvement must exceed this (set to the noise floor)")
    i.add_argument("--timeout", type=float, default=3600, help="harness timeout in seconds")
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_init)

    r = sub.add_parser("run", help="verify integrity, run the harness, record and judge")
    r.add_argument("--show", type=int, default=8, help="how many cases to list in each section")
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("check", help="verdict of the latest run")
    c.set_defaults(fn=cmd_check)

    s = sub.add_parser("status", help="run history")
    s.add_argument("--last", type=int, default=30)
    s.set_defaults(fn=cmd_status)

    rl = sub.add_parser("relock", help="accept a human-approved verifier change")
    rl.add_argument("--reason", help="who approved it and why")
    rl.set_defaults(fn=cmd_relock)

    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
