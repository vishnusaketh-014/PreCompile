import sqlite3
import json

conn = sqlite3.connect('/var/log/precompile/benefit_demo.db')
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT * FROM benefit_evals WHERE project_id='heavy' ORDER BY id DESC LIMIT 5").fetchall()
for r in rows:
    d = dict(r)
    print("ID:", d['id'], "action:", d['action'], "p_value:", d['p_value'], "overall_effect:", d['overall_effect'], "weight_after:", d['weight_after'], "arming_after:", d['arming_after'])
    print("  per_bin:", json.loads(d['per_bin_json']))
conn.close()
