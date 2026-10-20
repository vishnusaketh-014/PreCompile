#!/usr/bin/env python3
import json
import re
from pathlib import Path

results_path = Path("benchmarks/results/results.jsonl")
records = []
if results_path.exists():
    with open(results_path) as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

print("=== PILOT OUTLIER BUILDS IN RESULTS.JSONL ===")
for r in records:
    cond = r.get("condition")
    load = r.get("load_level")
    dur = float(r.get("duration_wall_s", 0))
    # Check c3-static moderate (4.234s) and c1-reactive moderate (2.91s) or high (2.11s)
    if (cond == "c3-static" and dur > 3.0) or (cond == "c1-reactive" and dur > 2.5):
        print(json.dumps(r, indent=2))

# Check pilot logs if available
log_file = Path("benchmarks/pilot_run.log")
if log_file.exists():
    print("\n=== RELEVANT LINES FROM PILOT_RUN.LOG ===")
    content = log_file.read_text(encoding="utf-8", errors="replace")
    for line in content.splitlines():
        if any(k in line for k in ["c3-static", "c1-reactive", "4.23", "2.91", "[claim]", "[build]", "psi="]):
            print(line)
