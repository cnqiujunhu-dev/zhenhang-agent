"""Recompute published counters from deidentified observations. No network."""
import argparse,json,statistics,math
from pathlib import Path
from collections import Counter
parser=argparse.ArgumentParser();parser.add_argument('data',type=Path);args=parser.parse_args()
d=json.loads(args.data.read_text('utf8'));q=d['quality_observations'];assert len(q)==60
ordinary=[r for r in q if not r['challenge']];assert len(ordinary)==48
assert sum(r['intent_correct'] for r in ordinary)==44
assert sum(r['scored_fields'] for r in ordinary)==sum(r['correct_fields'] for r in ordinary)==37
assert sum(r['scored_fields']>0 for r in ordinary)==18
assert Counter((r['intent_correct'],r['guard_accepted']) for r in q)==Counter({(True,True):48,(True,False):1,(False,True):6,(False,False):5})
for scene in d['quality']['scenes']:
    rr=[r for r in ordinary if r['scene']==scene['scene']]
    assert len(rr)==8 and sum(r['intent_correct'] for r in rr)==scene['intent']['correct']
rows=d['laya_observations'];assert len(rows)==80
for arm in ['ALL_PRO','ALL_FLASH','LAYA_MIXED','NO_LAYA_CONSERVATIVE']:
    rr=[r for r in rows if r['arm']==arm];m=d['laya']['metrics'][arm];assert len(rr)==20
    assert sum(r['raw_label_match'] is True for r in rr)==m['strict_label_matches']
    assert sum(r['labels_and_fields_match'] is True for r in rr)==m['strict_labels_and_fields_matches']
    assert abs(statistics.mean(r['total_wall_ms'] for r in rr)-m['latency']['mean_ms'])<1e-6
    assert abs(statistics.median(r['total_wall_ms'] for r in rr)-m['latency']['p50_ms'])<1e-6
    assert sorted(r['total_wall_ms'] for r in rr)[18]==m['latency']['p95_ms']
mixed=[r for r in rows if r['arm']=='LAYA_MIXED']
for trial in d['laya']['threshold_replay']:
    selected=[r for r in mixed if r['laya']['heads']['scene']['label']!='G' and r['laya']['heads']['difficulty']['label']=='A'
        and min(h['answer_confidence'] for h in r['laya']['heads'].values())>=trial['threshold']]
    assert len(selected)==trial['selected']
    assert sum(r['case_id'] not in {'BI-03','WE-01','WE-03','SU-02','SU-03'} for r in selected)==trial['outside_predeclared_simple_slice']
assert not d['new_live_inference'] and not d['laya']['production_adoption']
print(json.dumps({'checks_passed':True,'ordinary_cases':48,'semantic_cases':60,'laya_observations':80,'independent_laya_cloud_calls':60,'thresholds_checked':5}))
