#!/usr/bin/env python3
import json
import math
from pathlib import Path

results_file = Path("benchmarks/results/results.jsonl")
if not results_file.exists():
    print("No results.jsonl found")
    exit(0)

records = []
with open(results_file) as f:
    for line in f:
        line = line.strip()
        if line:
            records.append(json.loads(line))

print(f"Total pilot records: {len(records)}")

timestamps = [r["timestamp"] for r in records if "timestamp" in r]
durations = [r["duration_wall_s"] for r in records if "duration_wall_s" in r]

if timestamps:
    # Wall time from start of first build to end of last build
    wall_time = (max(timestamps) + durations[timestamps.index(max(timestamps))]) - min(timestamps)
    print(f"Pilot builds span: {wall_time:.1f}s ({wall_time/60:.2f} min)")

block_durations = {}
for r in records:
    b_id = (r.get("tier"), r.get("scenario"), r.get("condition"), r.get("load_level"), r.get("block_index"))
    block_durations.setdefault(b_id, []).append(r.get("duration_wall_s", 0.0))

block_totals = [sum(v) for v in block_durations.values()]
if block_totals:
    mean_block = sum(block_totals) / len(block_totals)
    med_block = sorted(block_totals)[len(block_totals) // 2]
    print(f"Total blocks in pilot: {len(block_totals)}")
    print(f"Mean block build duration: {mean_block:.2f}s, Median block build duration: {med_block:.2f}s")
    print(f"Min block duration: {min(block_totals):.2f}s, Max block duration: {max(block_totals):.2f}s")

# Let's see per-block details
print("\n--- Per-Block Summary ---")
for b_id, b_durs in sorted(block_durations.items()):
    tier, scen, cond, load, blk = b_id
    tot = sum(b_durs)
    print(f"Tier {tier} Scenario {scen} | {cond:12s} | load={load:8s} | block {blk}: sum={tot:6.2f}s, builds={[round(x, 2) for x in b_durs]}")

# Full run estimates:
# In full experiments:
# Tier 1 (Scenario A): 4 conditions (c0, c1, c2, c3) x 3 loads (idle, moderate, high) x 5 blocks x 3 builds = 60 blocks = 180 builds
# Tier 2 (Scenario A + Scenario B):
# Scenario A (long idle interval): c0, c2 x 3 loads x 5 blocks = 30 blocks = 90 builds
# Scenario B (bursty multi-save): c0, c2 x 3 loads x 5 blocks = 30 blocks = 90 builds
# Total Tier 2 = 60 blocks = 180 builds

# Let's compute average block time across all pilot blocks:
avg_block = sum(block_totals) / len(block_totals)
# Each block also has inter-build delays: in Tier 1, delay_mean = 1.8s, plus ~3s between blocks, plus 12s PSI settle time per condition-load change.
# Build time per block: ~13s (3 builds * ~4.3s)
# Plus delay between builds: 3 builds * 1.8s = 5.4s
# Daemon launch / PSI warmup per block: ~15s
# Total wall time per block ≈ block_build_sum + 5.4s + 15s = block_build_sum + ~20s
est_block_wall = avg_block + 5.4 + 15.0

print(f"\n--- Full-Run Time Estimation ---")
print(f"Average pure build duration per block: {avg_block:.1f}s")
print(f"Estimated wall time per block (including save delays, daemon launch, PSI stabilization): ~{est_block_wall:.1f}s")

tier1_blocks = 60
tier1_est_s = tier1_blocks * est_block_wall
print(f"Tier 1 (60 blocks): ~{tier1_est_s:.0f}s ({tier1_est_s/60:.1f} min / {tier1_est_s/3600:.2f} hr)")

tier2_blocks = 60  # 30 Scenario A + 30 Scenario B
tier2_est_s = tier2_blocks * est_block_wall
print(f"Tier 2 (60 blocks): ~{tier2_est_s:.0f}s ({tier2_est_s/60:.1f} min / {tier2_est_s/3600:.2f} hr)")

total_est_s = tier1_est_s + tier2_est_s
print(f"Total Full Benchmark (Tier 1 + Tier 2): ~{total_est_s:.0f}s ({total_est_s/60:.1f} min / {total_est_s/3600:.2f} hr)")

# Outlier builds: build_duration > 1.5 * cell median
print("\n--- OUTLIER BUILDS (> 1.5x median of condition-load cell) ---")
by_cell = {}
for r in records:
    cell_key = (r.get("tier"), r.get("condition"), r.get("load_level"))
    by_cell.setdefault(cell_key, []).append(r)

outliers = []
for cell_key, cell_records in by_cell.items():
    durs = [r["duration_wall_s"] for r in cell_records if "duration_wall_s" in r]
    if not durs:
        continue
    durs.sort()
    med = durs[len(durs) // 2]
    threshold = 1.5 * med
    for r in cell_records:
        dur = r.get("duration_wall_s", 0.0)
        if dur > threshold:
            outliers.append((r, med, dur, dur / med))

print(f"Found {len(outliers)} outlier builds:")
for r, med, dur, ratio in outliers:
    print(f"  Tier {r.get('tier')} {r.get('condition')} load={r.get('load_level')} block={r.get('block_index')} cycle={r.get('cycle')}: "
          f"dur={dur:.3f}s (cell median={med:.3f}s, ratio={ratio:.2f}x, decision={r.get('decision')}, claimed={r.get('claimed')})")
