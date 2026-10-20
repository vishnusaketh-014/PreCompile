"""Analyze build durations grouped by the reservation weight in force."""
from collections import defaultdict
from precompile.store import DataStore
from precompile.stats import median, percentile

def main():
    store = DataStore("/var/log/precompile/benefit_demo.db")
    builds = store.builds_by_condition("heavy", "reserved")
    res_list = store.list_reservations("heavy")
    holdouts = store.builds_by_condition("heavy", "holdout")
    
    print(f"Total reserved builds: {len(builds)}")
    print(f"Total holdout builds:  {len(holdouts)}")
    print(f"Total reservations:    {len(res_list)}")
    
    print("Sample build:", builds[0])
    print("Sample res:", res_list[0])
    
    by_weight = defaultdict(list)
    for b in builds:
        matched_w = None
        for r in res_list:
            if r["prediction_id"] and b.prediction_id and r["prediction_id"] == b.prediction_id:
                matched_w = r["weight"]
                break
        if matched_w is None and len(res_list) == len(builds):
            # 1-to-1 matching by order
            idx = builds.index(b)
            matched_w = res_list[idx]["weight"]
        if matched_w is not None:
            by_weight[matched_w].append(b.duration_s)
        else:
            by_weight["unknown"].append(b.duration_s)
            
    print("\nReserved builds grouped by weight:")
    for w in sorted(by_weight.keys(), key=lambda x: (str(x))):
        durs = by_weight[w]
        med = median(durs)
        p95 = percentile(durs, 95.0)
        print(f"  Weight {w}: n={len(durs)}, median={med:.3f}s, p95={p95:.3f}s, durs={[round(x, 2) for x in durs]}")
        
    holdout_durs = [b.duration_s for b in holdouts]
    print(f"\nHoldout builds: n={len(holdout_durs)}, median={median(holdout_durs):.3f}s, p95={percentile(holdout_durs, 95.0):.3f}s")
    store.close()

if __name__ == "__main__":
    main()
