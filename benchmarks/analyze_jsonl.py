#!/usr/bin/env python3
import json

with open("/tmp/ab_smoke_harness.jsonl") as f:
    records = [json.loads(line) for line in f]

reserved = [r for r in records if r["duration_s"] < 6.0]
holdout = [r for r in records if r["duration_s"] >= 6.0]

def stats(values):
    s = sorted(values)
    n = len(s)
    med = s[n // 2]
    mean = sum(s) / n
    return med, mean

res_cpu_share = [r["cpu_share"] for r in reserved]
hol_cpu_share = [r["cpu_share"] for r in holdout]
res_cpu_s = [r["cpu_s"] for r in reserved]
hol_cpu_s = [r["cpu_s"] for r in holdout]
res_dur = [r["duration_s"] for r in reserved]
hol_dur = [r["duration_s"] for r in holdout]

r_cs_med, r_cs_mean = stats(res_cpu_share)
h_cs_med, h_cs_mean = stats(hol_cpu_share)
r_dur_med, r_dur_mean = stats(res_dur)
h_dur_med, h_dur_mean = stats(hol_dur)

print("Harness JSONL Metrics (n_reserved=%d, n_holdout=%d):" % (len(reserved), len(holdout)))
print("  Reserved: Duration median=%.3fs, CPU share median=%.1f%% (mean=%.1f%%)" % (r_dur_med, r_cs_med * 100, r_cs_mean * 100))
print("  Holdout:  Duration median=%.3fs, CPU share median=%.1f%% (mean=%.1f%%)" % (h_dur_med, h_cs_med * 100, h_cs_mean * 100))
