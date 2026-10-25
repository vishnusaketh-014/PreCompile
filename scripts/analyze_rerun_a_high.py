import json
import math
import statistics
import sys
from pathlib import Path

path = Path("benchmarks/results_rerun_scen_a_high/results.jsonl")
if not path.exists():
    print(f"Error: {path} not found")
    sys.exit(1)

rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
print(f"Total measured builds loaded: {len(rows)}")

cond_delays = {}
cond_walls = {}
for r in rows:
    c = r["condition"]
    cond_delays.setdefault(c, []).append(r["delay_s"])
    cond_walls.setdefault(c, []).append(r["build_wall_s"])

all_delays = [r["delay_s"] for r in rows]

def print_dist(name, data):
    n = len(data)
    mean = statistics.mean(data)
    sd = statistics.stdev(data) if n > 1 else 0.0
    cv = sd / mean if mean > 0 else 0.0
    mn = min(data)
    mx = max(data)
    print(f"=== {name} (N={n}) ===")
    print(f"Mean: {mean:.4f}s | SD: {sd:.4f}s | CV: {cv:.4f} | Min: {mn:.4f}s | Max: {mx:.4f}s")
    bins = 10
    step = (mx - mn) / bins if mx > mn else 1.0
    counts = [0] * bins
    for v in data:
        idx = min(bins - 1, int((v - mn) / step)) if mx > mn else 0
        counts[idx] += 1
    max_c = max(counts) if counts else 1
    print("Distribution Histogram:")
    for i in range(bins):
        low = mn + i * step
        high = mn + (i + 1) * step
        bar = "#" * int(40 * counts[i] / max_c)
        print(f"  [{low:5.2f} - {high:5.2f}s]: {counts[i]:3d} | {bar}")
    print()

print_dist("Scenario A High-Load Realised Delays (All Conditions, N=200)", all_delays)

print("=== Per-Condition Delay Distribution ===")
for cond in sorted(cond_delays.keys()):
    print_dist(f"Delay: {cond}", cond_delays[cond])

print("=== Per-Condition Build Wall Stats ===")
print(f"{'Condition':<15} | {'N':>3} | {'Median':>7} | {'Mean':>7} | {'P95':>7} | {'Min':>7} | {'Max':>7}")
print("-" * 65)
for cond in sorted(cond_walls.keys()):
    vals = sorted(cond_walls[cond])
    n = len(vals)
    med = statistics.median(vals)
    mean = statistics.mean(vals)
    p95_idx = int(math.ceil(0.95 * n)) - 1
    p95 = vals[min(p95_idx, n - 1)]
    mn = min(vals)
    mx = max(vals)
    print(f"{cond:<15} | {n:>3} | {med:>7.4f} | {mean:>7.4f} | {p95:>7.4f} | {mn:>7.4f} | {mx:>7.4f}")

# Decompose C2 into reserved vs holdout
c2_rows = [r for r in rows if r["condition"] == "c2-full"]
c2_reserved = [r["build_wall_s"] for r in c2_rows if not r.get("is_holdout", False)]
c2_holdout = [r["build_wall_s"] for r in c2_rows if r.get("is_holdout", False)]
print("\n=== C2 Decomposed Build Wall Stats ===")
print(f"{'Subgroup':<15} | {'N':>3} | {'Median':>7} | {'Mean':>7} | {'P95':>7} | {'Min':>7} | {'Max':>7}")
print("-" * 65)
for name, vals in [("C2 (ITT)", [r["build_wall_s"] for r in c2_rows]), ("C2 Reserved", c2_reserved), ("C2 Holdout", c2_holdout)]:
    vals_s = sorted(vals)
    n = len(vals_s)
    med = statistics.median(vals_s) if n > 0 else 0
    mean = statistics.mean(vals_s) if n > 0 else 0
    p95_idx = int(math.ceil(0.95 * n)) - 1
    p95 = vals_s[min(p95_idx, n - 1)] if n > 0 else 0
    mn = min(vals_s) if n > 0 else 0
    mx = max(vals_s) if n > 0 else 0
    print(f"{name:<15} | {n:>3} | {med:>7.4f} | {mean:>7.4f} | {p95:>7.4f} | {mn:>7.4f} | {mx:>7.4f}")
