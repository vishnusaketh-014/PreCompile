import sqlite3
import json

conn = sqlite3.connect('/var/log/precompile/benefit_demo.db')
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT * FROM benefit_evals WHERE project_id='heavy' ORDER BY id ASC").fetchall()
print("=== ALL BENEFIT EVALUATIONS ===")
for r in rows:
    d = dict(r)
    print(f"Eval #{d['id']}: action={d['action']:<7} weight_after={d['weight_after']} arming_after={d['arming_after']} effect={d['overall_effect']*100:5.1f}% 95%CI=[{d['ci_low']*100:5.1f}%, {d['ci_high']*100:5.1f}%] p={d['p_value']:.4f}")
    pb = json.loads(d['per_bin_json'])
    for b_name, b_data in pb.items():
        if b_data.get('median_reserved') is not None:
            print(f"   Bin {b_name:<8}: res_n={b_data['n_reserved']} hld_n={b_data['n_holdout']} res_med={b_data['median_reserved']:.2f}s hld_med={b_data['median_holdout']:.2f}s eff={b_data['effect']*100:5.1f}% p={b_data['p_value']:.4f}")
        else:
            print(f"   Bin {b_name:<8}: res_n={b_data['n_reserved']} hld_n={b_data['n_holdout']} (insufficient samples)")

conn.close()
