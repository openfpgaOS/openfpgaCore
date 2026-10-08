#!/usr/bin/env python3
"""Parse actual STA full-path exports, keeping skew/setup in period bounds."""
import json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'

def parse(p):
    out=[]
    for b in re.split(r'\nPath #\d+:',p.read_text())[1:]:
        def field(name):
            m=re.search(r';\s*'+re.escape(name)+r'\s*;\s*([^;]+);',b);assert m,name
            return m[1].strip()
        slack=float(field('Slack').split()[0]);relationship=float(field('Setup Relationship'))
        m=re.search(r';\s*Number of Logic Levels\s*;\s*;\s*(\d+)\s*;',b);assert m
        data=re.split(r';\s*data path\s*;',b,flags=re.I)[1].split('Data Required Path')[0]
        route=[]
        for line in data.splitlines():
            row=[x.strip() for x in line.split(';')][1:-1]
            if len(row)>=7 and row[3]=='IC':route.append(float(row[1]))
        out.append(dict(from_node=field('From Node'),to_node=field('To Node'),slack_ns=slack,
            relationship_ns=relationship,effective_period_ns=round(relationship-slack,3),
            period_bound_mhz=round(1000/(relationship-slack),3) if relationship==10.0 else None,data_delay_ns=float(field('Data Delay')),
            logic_levels=int(m[1]),route_hops=len(route),route_delay_ns=round(sum(route),3)))
    return out

def main():
    label=sys.argv[1];d=P/label
    parsed={p.stem:parse(p) for p in sorted((d/'reports').glob('*.rpt'))}
    assert parsed and all(parsed.values())
    dest=d/'paths.json'
    if dest.exists():
        previous=json.loads(dest.read_text());assert parsed==previous
    else:dest.write_text(json.dumps(parsed,indent=2)+'\n')
    for name,rows in parsed.items():print(name,len(rows),json.dumps(rows[0]))
if __name__=='__main__':main()
