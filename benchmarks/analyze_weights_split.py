"""Analyze build durations grouped by weight, split by idle vs contention."""
from collections import defaultdict
from precompile.store import DataStore
from precompile.stats import median, percentile

def main():
    store = DataStore("/var/log/precompile/benefit_demo.db")
    builds = store.builds_by_condition("heavy", "reserved")
    res_list = store.list_reservations("heavy")
    holdouts = store.builds_by_condition("heavy", "holdout")

    # Map reservation weight by prediction_id
    res_by_pred = {r["prediction_id"]: r["weight"] for r in res_list if r.get("prediction_id")}

    by_weight_contention = defaultdict(list)
    by_weight_idle = defaultdict(list)

    for b in builds:
        w = res_by_pred.get(b.prediction_id, "unknown")
        cl = b.contention_level or 0.0
        if cl >= 60.0:
            by_weight_contention[w].append(b.duration_s)
        else:
            by_weight_idle[w].append(b.duration_s)

    print("=== HIGH CONTENTION BUILDS BY RESERVATION WEIGHT ===")
    for w in sorted(by_weight_contention.keys(), key=lambda x: (str(x))):
        durs = by_weight_contention[w]
        med = median(durs)
        p95 = percentile(durs, 95.0)
        print(f"  Weight {w}: n={len(durs)}, median={med:.3f}s, p95={p95:.3f}s, durs={[round(x, 2) for x in durs]}")

    hld_cont = [b.duration_s for b in holdouts if (b.contention_level or 0.0) >= 60.0]
    print(f"  Holdouts (unboosted, weight 100): n={len(hld_cont)}, median={median(hld_cont):.3f}s, p95={percentile(hld_cont, 95.0):.3f}s")

    print("\n=== IDLE BUILDS BY RESERVATION WEIGHT ===")
    for w in sorted(by_weight_idle.keys(), key=lambda x: (str(x))):
        durs = by_weight_idle[w]
        med = median(durs)
        p95 = percentile(durs, 95.0)
        print(f"  Weight {w}: n={len(durs)}, median={med:.3f}s, p95={p95:.3f}s, durs={[round(x, 2) for x in durs]}")

    hld_idle = [b.duration_s for b in holdouts if (b.contention_level or 0.0) < 60.0]
    print(f"  Holdouts (idle): n={len(hld_idle)}, median={median(hld_idle):.3f}s, p95={percentile(hld_idle, 95.0):.3f}s")
    store.close()

if __name__ == "__main__":
    main()
