import sqlite3
import statistics

conn = sqlite3.connect('/var/log/precompile/benefit_demo.db')
conn.row_factory = sqlite3.Row

rows = conn.execute("SELECT condition, duration_s, contention_level FROM builds WHERE condition IN ('reserved', 'holdout')").fetchall()

p1_res = [r['duration_s'] for r in rows if r['condition'] == 'reserved' and (r['contention_level'] or 0) < 10]
p1_hld = [r['duration_s'] for r in rows if r['condition'] == 'holdout' and (r['contention_level'] or 0) < 10]
p2_res = [r['duration_s'] for r in rows if r['condition'] == 'reserved' and (r['contention_level'] or 0) >= 60]
p2_hld = [r['duration_s'] for r in rows if r['condition'] == 'holdout' and (r['contention_level'] or 0) >= 60]

def p95(arr):
    if not arr: return 0.0
    s = sorted(arr)
    k = (len(s) - 1) * 0.95
    f = int(k)
    c = min(f + 1, len(s) - 1)
    d = k - f
    return s[f] + d * (s[c] - s[f])

print("=== PER-PHASE SUMMARY ===")
def stats(name, arr):
    if not arr: return f"{name}: n=0"
    return f"{name}: n={len(arr)} median={statistics.median(arr):.3f}s p95={p95(arr):.3f}s mean={statistics.mean(arr):.3f}s min={min(arr):.3f}s max={max(arr):.3f}s"

print("Phase 1 (Idle):")
print("  ", stats("Reserved", p1_res))
print("  ", stats("Holdout ", p1_hld))

print("Phase 2 (High Contention):")
print("  ", stats("Reserved", p2_res))
print("  ", stats("Holdout ", p2_hld))

conn.close()
