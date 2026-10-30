import sys
sys.path.insert(0, ".")
import json
from dashboard.reader import DashboardDataReader
from precompile.stats import median

# Load raw JSONL
raw_durs = {'high': {}, 'moderate': {}, 'idle': {}}
with open('benchmarks/results/results_all.jsonl', 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get('scenario') == 'A' and not rec.get('is_warmup'):
            load = rec.get('load_level')
            cond = rec.get('condition')
            dur = rec.get('build_wall_s') or rec.get('duration_wall_s')
            if load in raw_durs and dur:
                raw_durs[load].setdefault(cond, []).append(float(dur))

reader = DashboardDataReader()
print(f'{"Load":<10} | {"Arm":<14} | {"UI Median":<10} | {"Raw JSONL Med":<14} | {"Delta":<8} | {"Speedup UI":<12}')
print('-' * 80)
for load in ['high', 'moderate', 'idle']:
    ui_data = reader.get_compare_data('A', load)
    for arm, key in [('C0 (Without)', 'without'), ('C2 (With)', 'with_precompile'), ('C1 (Reactive)', 'reactive_only')]:
        ui_med = ui_data[key]['median_s']
        cond_key = 'c0-baseline' if 'C0' in arm else ('c2-full' if 'C2' in arm else 'c1-reactive')
        raw_med = round(median(raw_durs[load][cond_key]), 3)
        delta = round(abs(ui_med - raw_med), 4)
        sp = f"{ui_data['speedup']['relative_pct']}%" if 'C2' in arm else '-'
        print(f'{load:<10} | {arm:<14} | {ui_med:<10.3f} | {raw_med:<14.3f} | {delta:<8} | {sp:<12}')
