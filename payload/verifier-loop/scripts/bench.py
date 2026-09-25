#!/usr/bin/env python3
"""Run a command repeatedly and report latency percentiles as harness JSON.

    bench.py --runs 50 --warmup 5 -- node scripts/render.js big.pptx
    bench.py --runs 200 --metric p99 --shell "curl -s -o /dev/null http://localhost:3000/api/x"

Prints one JSON object (last line of stdout):
    {"score": <chosen metric in ms>, "p50": .., "p90": .., "p99": .., "mean": .., "min": .., "max": ..,
     "stdev": .., "runs": N, "failures": K}

A run with a non-zero exit code counts as a failure. By default any failure makes the
score null and the exit code 1, because a fast wrong answer is not a speed-up; pass
--allow-failures to only report them.
"""
import argparse
import json
import shlex
import statistics
import subprocess
import sys
import time


def percentile(sorted_vals, q):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * q / 100.0
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=30)
    ap.add_argument("--warmup", type=int, default=3, help="untimed runs first (default: %(default)s)")
    ap.add_argument("--metric", choices=["p50", "p90", "p99", "mean", "max"], default="p90")
    ap.add_argument("--timeout", type=float, default=300, help="seconds per run")
    ap.add_argument("--allow-failures", action="store_true")
    ap.add_argument("--shell", help="command string run through the shell (alternative to -- cmd ...)")
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="command after --")
    args = ap.parse_args()

    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    if args.shell:
        target, use_shell = args.shell, True
    elif cmd:
        target, use_shell = cmd, False
    else:
        ap.error("give a command after -- or via --shell")

    def once():
        t0 = time.perf_counter()
        try:
            rc = subprocess.run(target, shell=use_shell, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=args.timeout).returncode
        except subprocess.TimeoutExpired:
            rc = -1
        return (time.perf_counter() - t0) * 1000.0, rc

    for _ in range(args.warmup):
        once()

    times, failures = [], 0
    for _ in range(args.runs):
        ms, rc = once()
        if rc != 0:
            failures += 1
        else:
            times.append(ms)

    times.sort()
    stats = {
        "p50": percentile(times, 50), "p90": percentile(times, 90), "p99": percentile(times, 99),
        "mean": statistics.fmean(times) if times else None,
        "min": times[0] if times else None, "max": times[-1] if times else None,
        "stdev": statistics.stdev(times) if len(times) > 1 else 0.0,
    }
    stats = {k: (round(v, 3) if v is not None else None) for k, v in stats.items()}
    ok = times and (failures == 0 or args.allow_failures)
    result = {"score": stats[args.metric] if ok else None, **stats, "runs": args.runs, "failures": failures,
              "command": target if isinstance(target, str) else shlex.join(target)}
    print(json.dumps(result))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
