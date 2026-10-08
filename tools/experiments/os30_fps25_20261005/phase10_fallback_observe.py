#!/usr/bin/env python3
"""Passive actual-ELF classification of selected overlap-v1 fallbacks."""
import argparse, collections, json, os, shutil, struct, subprocess
from pathlib import Path
from elftools.elf.elffile import ELFFile
from phase10_experiment import CAMP, ROOT, module, sha, write

H = Path(__file__).resolve().parent
P = CAMP / 'phase10'
APP = P / 'load-submit-overlap-v1'
BASE = P / 'load-submit-functional-v1'
OUT = P / 'fallback-observe-v1'


def rep(t, a, b):
    assert t.count(a) == 1, (a, t.count(a))
    return t.replace(a, b)


def prepare():
    OUT.mkdir(exist_ok=False)
    q = OUT / 'functional-qsim'
    q.mkdir()
    for p in (BASE / 'functional-qsim').iterdir():
        if p.suffix in ('.c', '.h') or p.name == 'Makefile':
            shutil.copy2(p, q / p.name)
    shutil.copy2(APP / 'app.elf', OUT / 'app.elf')
    with (APP / 'app.elf').open('rb') as f:
        sym = {s.name: int(s['st_value']) for s in ELFFile(f).get_section_by_name('.symtab').iter_symbols()}
    names = ['gfx_clip_triangle.constprop.0', 'gfx_push_triangle.constprop.0.isra.0']
    asm = subprocess.check_output(['llvm-objdump', '-d', '--mattr=+m,+a,+f,+c', '--no-show-raw-insn',
                                  '--disassemble-symbols=' + ','.join(names), str(APP / 'app.elf')], text=True)
    (OUT / 'sites.asm').write_text(asm)
    assert sym[names[0]] == 0x104d12c0 and sym[names[1]] == 0x104d0000
    assert '104d0062:' in asm and 'beqz\ts8,' in asm
    globals_ = ['triangle_vc_elig', 'triangle_vc_rgb_input', 'triangle_vc_alpha_input', 'g_decal', 'g_z_write']
    defines = '\n'.join('#define FBO_' + n.upper() + ' 0x%xu' % sym[n] for n in globals_)
    observer = defines + r'''
/* Records are LE32 host words: frame,type,elig,rgb,alpha,decal,zwrite,
 * three source addresses and three complete36-byte LoadedVertex values. */
static void fallback_observe(qenv *E,qsim_cpu *cpu,uint32_t pc){
 static FILE *f;
 if(!f){const char*p=getenv("FALLBACK_OBSERVER_PATH");if(!p)abort();f=fopen(p,"wb");if(!f)abort();}
 if(E->flips<360||E->flips>720)return;
 uint32_t w[37]={0};w[0]=E->flips;w[1]=pc==0x104d12c0u?1:2;
 w[2]=rd32(E,FBO_TRIANGLE_VC_ELIG);w[3]=rd32(E,FBO_TRIANGLE_VC_RGB_INPUT);w[4]=rd32(E,FBO_TRIANGLE_VC_ALPHA_INPUT);
 w[5]=rd32(E,FBO_G_DECAL);w[6]=rd32(E,FBO_G_Z_WRITE);
 unsigned reg[3]={10,11,12};if(w[1]==2){reg[0]=23;reg[1]=9;reg[2]=18;if(w[2]!=cpu->x[24])fatal(E,"eligibility site disagrees");}
 for(unsigned i=0;i<3;i++){w[7+i]=cpu->x[reg[i]];memcpy(w+10+i*9,gp(E,w[7+i],36),36);}
 if(fwrite(w,sizeof w,1,f)!=1)abort();
}
'''
    (q / 'fallback_observer.h').write_text(observer)
    p = q / 'overlap_observer.h'
    t = p.read_text()
    t = '#include "fallback_observer.h"\n' + t
    t = rep(t, ' qenv *E=ctx;unsigned n=obs_count(E);',
            ' qenv *E=ctx;if(pc==0x104d12c0u||pc==0x104d0062u){fallback_observe(E,cpu,pc);return;}unsigned n=obs_count(E);')
    p.write_text(t)
    p = q / 'cpu_exec.h'
    t = p.read_text()
    t = rep(t, 'e->h && (', 'e->h && (e->pc==0x104d12c0u || e->pc==0x104d0062u || ')
    p.write_text(t)
    with (OUT / 'build.log').open('x') as f:
        subprocess.run(['make', '-j4'], cwd=q, stdout=f, stderr=subprocess.STDOUT, check=True)
    write(OUT / 'prepared.json', dict(elf_sha256=sha(APP / 'app.elf'), sources={p.name: sha(p) for p in q.iterdir() if p.suffix in ('.c','.h') or p.name=='Makefile'}, simulator_sha256=sha(q/'qsim'), symbols=sym, helper_sha256=sha(Path(__file__)), scope='Passive observation only; no instructions replaced, no modeled cycles changed. Synthetic LOAD functional latency.'))


def capture():
    m = module('fallback_capture', H / 'phase10_load_cram_capture.py')
    m.OUT = OUT
    m.CAND = APP
    os.environ['LOD5_RECYCLE_STATS_ADDR'] = hex(m.symbols(APP/'app.elf')['cram_vertex_recycle_stats'])
    os.environ['LOAD_SUBMIT_OBSERVER_PATH'] = str(OUT/'overlap-observer.csv')
    os.environ['FALLBACK_OBSERVER_PATH'] = str(OUT/'fallback.bin')
    m.capture('normal')


def analyze():
    before, after = BASE/'capture-normal', OUT/'capture-normal'
    noninterference = {}
    for name in ('frames.csv','scene.csv','geometry.csv','culling.csv'):
        assert sha(before/name)==sha(after/name),name
        noninterference[name]=sha(after/name)
    for p in (before/'gpuvec').glob('*.vec'):
        target=after/'gpuvec'/p.name
        assert sha(p)==sha(target),p.name
        noninterference['gpuvec/'+p.name]=sha(target)
    prep=json.loads((OUT/'prepared.json').read_text())
    vertexbase=prep['symbols']['rsp']+0x3ac
    raw=(OUT/'fallback.bin').read_bytes();assert len(raw)%148==0
    windows={name:dict(clip=collections.Counter(),push=collections.Counter(),planes=collections.Counter()) for name in ('warm8','gameplay361')}
    examples={}
    def f32(u):return struct.unpack('<f',struct.pack('<I',u))[0]
    for offset in range(0,len(raw),148):
        r=struct.unpack_from('<37I',raw,offset);frame,kind,elig,_,_,decal,zwrite=r[:7]
        vertices=[r[10+9*i:19+9*i] for i in range(3)]
        clips=[v[8]&255 for v in vertices];clipor=clips[0]|clips[1]|clips[2]
        ws=[f32(v[3]) for v in vertices]
        validw=all(w>0 and w<32767 for w in ws)
        equalw=ws[0]==ws[1]==ws[2]
        stable=all(vertexbase<=a<vertexbase+1152 for a in r[7:10])
        for name in ('gameplay361',)+(('warm8',)if 360<=frame<=367 else ()):
            group=windows[name]['clip'if kind==1 else'push'];group['calls']+=1
            if kind==1:
                reason='inside'if not clipor else'xy_only'if not(clipor&3)else'depth_only'if not(clipor&60)else'xy_and_depth'
                group[reason]+=1;windows[name]['planes'][str(clipor)]+=1
                if clipor:
                    group['positive_bounded_w'if validw else'nonpositive_or_large_w']+=1
                    group['equal_w'if equalw else'varying_w']+=1
                    if reason=='xy_only'and validw:group['xy_only_positive_bounded_w']+=1
                    if reason=='xy_only'and len(set(v[7]for v in vertices))>1:group['xy_only_varying_rgba']+=1
                    if reason not in examples:examples[reason]=dict(frame=frame,record_index=offset//148,words=list(r))
            else:
                reason='material'if not elig else'noncache_slot'if not stable else'w_range'if not validw else'equal_w'if equalw else'cache_eligible'
                group[reason]+=1
                if not elig:group['material_decal'if decal else'material_other']+=1;group['material_zwrite'if zwrite else'material_no_zwrite']+=1
    write(OUT/'result.json',dict(passed=True,passive=True,elf_sha256=prep['elf_sha256'],records=len(raw)//148,noninterference=noninterference,windows=windows,examples=examples,limits='Classifies actual clipper entry and post-material push site. Push calls include generated clip-fan triangles and rectangles; they are not one-for-one original rejected commands. Material subdivisions use captured flags, not a complete combiner cause proof. No clipping bypass implementation or output-exactness claim.'))
    print(json.dumps(windows,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','capture','analyze']);a=p.parse_args();globals()[a.action]()
