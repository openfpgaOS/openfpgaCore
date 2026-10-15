#!/usr/bin/env python3
"""Bind immutable mesh inputs to exact passive native LOAD contexts/results."""
from pathlib import Path
import argparse,collections,csv,hashlib,json,struct
H=Path(__file__).resolve().parent;M=H.parent;P=M.parent
ap=argparse.ArgumentParser();ap.add_argument('--trace',default='native-task-v3');ap.add_argument('--out',default='native-vectors-v3');args=ap.parse_args()
T=M/'review'/args.trace;R=T/'run';O=H/args.out;O.mkdir(exist_ok=False)
ELF=P/'queued-front-v1/work/sm64-v2/.obj/sm64-queued-front-v2-perfdiag/app.elf'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def table(n):
    with (R/n).open() as f:return [{k:int(v) for k,v in r.items()} for r in csv.DictReader(f,delimiter='\t')]
passivity=json.loads((T/'passivity.json').read_text());assert passivity['status']=='PASS_EXACT_PASSIVITY'
layout=json.loads((H/'layout-v2.json').read_text())['fields'];assert layout['Vtx_size']==24
entries={x['task']:x for x in table('task-entries.tsv')};states={x['task']:x for x in table('task-state.tsv')}
contexts=table('task-raw-contexts.tsv');raw={x['task']:x for x in table('task-raw.tsv')}
commands=collections.defaultdict(list);outputs=collections.defaultdict(list)
for x in table('task-commands.tsv'):commands[x['task']].append(x)
for x in table('task-raw-vertices.tsv'):outputs[x['task']].append(x)
classes={x['task']:x for x in json.loads((T/'tasks-classified.json').read_text())}
physical=(R/'task-raw-inputs.bin').read_bytes();statebytes=(R/'task-state.bin').read_bytes()
eb=ELF.read_bytes();h=struct.unpack_from('<16sHHIIIIIHHHHHH',eb)
sections=[struct.unpack_from('<IIIIIIIIII',eb,h[6]+h[11]*i) for i in range(h[12])]
def asset(a,n):
    for s in sections:
        if s[1]!=8 and s[2]&2 and s[3]<=a and a+n<=s[3]+s[5]:return eb[s[4]+a-s[3]:s[4]+a-s[3]+n]
    raise AssertionError(('source outside file-backed ELF',hex(a),n))
def u32(b,o):return struct.unpack_from('<I',b,o)[0]
def packed_rgb(b,o):return int.from_bytes(b[o:o+3],'little')
vectors=[];checks=collections.Counter();byphase=collections.Counter()
for c in contexts:
    ident=c['task'];e=entries[ident];s=states[ident];r=raw[ident];count=c['count'];assert count==e['count']==r['count']
    # Physical source dump must have full port-specific six-word input rows.
    inp=physical[c['data_offset']:c['data_offset']+count*24];assert len(inp)==count*24
    assert inp==asset(e['source'],count*24),(ident,'physical source differs from immutable ELF')
    checks['physical_source_24B_rows']+=count
    assert s['rsp_valid']==s['rdp_valid']==1
    rsp=statebytes[s['rsp_offset']:s['rsp_offset']+940];rdp=statebytes[s['rdp_offset']:s['rdp_offset']+104]
    assert len(rsp)==940 and len(rdp)==104
    matrix=[u32(rsp,layout['RSP_MP_matrix']+4*i) for i in range(16)]
    assert matrix==[c['m'+str(i)] for i in range(16)],(ident,'matrix')
    checks['matrix_words']+=16
    sc=layout['RSP_scales'];su,sv=struct.unpack_from('<HH',rsp,sc)
    assert (su,sv)==(c['scale_u'],c['scale_v']),(ident,'UV scales');checks['scale_pairs']+=1
    geom=u32(rsp,layout['RSP_geometry_mode']);lit=bool(geom&0x20000)
    assert lit==bool(c['lit']);checks['lighting_modes']+=1
    if lit:
        coeff=[u32(rsp,layout['RSP_coefficients']+4*i) for i in range(3)]
        assert coeff==[c['cx'],c['cy'],c['cz']],(ident,'light coefficient')
        assert c['diffuse']==packed_rgb(rsp,layout['RSP_lights'])
        assert c['ambient']==packed_rgb(rsp,layout['RSP_lights']+12)
        checks['lit_contexts']+=1
    rows=sorted(outputs[ident],key=lambda x:x['slot']);assert len(rows)==count
    assert [x['slot'] for x in rows]==list(range(count));checks['raw_result_rows']+=count
    assert not r['unsupported'] and not r['dma_error'];checks['successful_raw_jobs']+=1
    cmd=commands[ident]
    if len(cmd)==1:continue
    assert all(x['address']==e['dl']+i*8 for i,x in enumerate(cmd))
    assert cmd[0]['w0']>>24==1 and cmd[0]['w1']==e['source']
    assert all(x['w0']>>24 in (5,6) for x in cmd[1:])
    cl=classes[ident];assert cl['raw_success'];byphase[e['phase']]+=1
    vectors.append({'native_task':ident,'phase':e['phase'],'span_start':e['dl'],'next_pointer':cl['end'],'source_cpu':e['source'],'source_psram':c['source_base'],'vertex_stride_bytes':24,'count':count,'destination':e['first'],'commands_u32':[w for x in cmd for w in [x['w0'],x['w1']]],'source_vertex_bytes_hex':inp.hex(),'matrix_u32':matrix,'aspect_u32':c['aspect'],'coefficient_u32':[c['cx'],c['cy'],c['cz']],'ambient':c['ambient'],'diffuse':c['diffuse'],'scale_u':su,'scale_v':sv,'lit':int(lit),'geometry_mode':geom,'cull_mode':(1 if geom&0x200 else 0)|(2 if geom&0x400 else 0),'raw_expected_u32':[[x[k] for k in ['x','y','z','w','invw','uv','rgba','clip']] for x in rows],'triangles':cl['triangles'],'conservative_geometry_supported':cl['conservative_geometry_supported'],'conservative_exceptions':cl['conservative_exceptions'],'rdp_state_bytes_hex':rdp.hex(),'rsp_prefix_bytes_hex':rsp.hex(),'material_ready_at_load':e['material_ready'],'material_elig_at_load':e['material_elig'],'material_binding':'Raw RDP snapshot only; ready/elig at LOAD can be cold or stale. Prepared shader/texture side effects and final raster state are not bound by this vector.'})
assert len(contexts)==652 and byphase=={1:319,2:319}
payload=O/'vectors.json';payload.write_text(json.dumps({'format':'mesh-native-vectors-v1','vectors':vectors},indent=2)+'\n')
inputs=[Path(__file__).resolve(),ELF,H/'layout-v2.json',T/'passivity.json',T/'tasks-classified.json']+[R/n for n in ['task-entries.tsv','task-state.tsv','task-state.bin','task-raw-contexts.tsv','task-raw-inputs.bin','task-commands.tsv','task-raw.tsv','task-raw-vertices.tsv']]
record={'status':'PASS_NATIVE_IMMUTABLE_SOURCE_AND_RAW_CONTEXT_JOIN','trace':str(T),'checks':dict(checks),'vectors':len(vectors),'vectors_by_phase':dict(byphase),'vectors_sha256':sha(payload),'inputs_sha256':{str(p):sha(p) for p in inputs},'scope':'Every accepted raw LOAD physical24B source row matches immutable ELF; matrix/light/scales match independently compiled software layout snapshot; vectors include exact raw hardware output rows. Arithmetic replay and material admission are separate proofs. Both intervals are native measured captures, not inferred warm snapshots.'}
(O/'manifest.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2))
