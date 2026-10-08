#!/usr/bin/env python3
"""Compare accepted-load lazy recovery to the original geometry functions."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3]

def structure(source,name):
    start=source.index('struct '+name+' {');end=source.index('\n};',start)+3
    return source[start:end]

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out-name',default='lazy-fixed');a=ap.parse_args()
    dest=HERE/'results'/a.out_name;dest.mkdir(exist_ok=False)
    control=HERE/'results/lazy-cpu-fixed-control'
    base=(control/'test_lazy.c').read_text();prefix=base[:base.index('/* Harness tail:')]
    source_dir=ROOT/'docs/measurements/os30-frontend-20261004/SM64/src/sm64/sm64/src/pc/gfx'
    geometry=(source_dir/'gfx_geometry.h').read_text();attributes=(source_dir/'gfx_geometry_attributes.h').read_text()
    reference='\n'+structure(geometry,'GeometryContext')+'\n'+structure(geometry,'GeometryVertex')+'\n'+structure(attributes,'GeometryAttributes')+'''
static struct GeometryContext geometry_contexts[1];
static struct GeometryVertex geometry_vertices[32];
static struct GeometryAttributes geometry_attributes[1];
static uint32_t geometry_pending,geometry_attribute_pending;
static unsigned sm64_geometry_fallbacks;
'''
    reference+=attributes[attributes.index('static void geometry_attributes_materialize('):]
    reference+='\nstatic void gfx_materialize_vertex_attributes(unsigned slot) {geometry_attributes_materialize(1u<<slot);}\n'
    reference+=geometry[geometry.index('static void geometry_materialize('):geometry.index('static inline __attribute__((always_inline)) int geometry_reject(')]
    prefix=prefix.replace('#include "sm64_lazy_state.h"',reference+'\n#include "sm64_lazy_state.h"')
    tail=(HERE/'test_lazy.c').read_text()
    tail=tail.replace('    original_vertices(count,first,v,aspect);',r'''
    struct GeometryContext *g=&geometry_contexts[0];
    const unsigned columns[3]={0,1,3};
    for(unsigned row=0;row<3;row++)for(unsigned k=0;k<4;k++) {
        float value=rsp.MP_matrix[k][columns[row]]*(row==0?aspect:1.0f);
        g->matrix[row*4+k]=(int32_t)lrintf(value*(k==3?256.0f:16777216.0f));
    }
    for(unsigned k=0;k<4;k++){g->z[k]=rsp.MP_matrix[k][2];g->fog_w[k]=rsp.MP_matrix[k][3];}
    for(unsigned i=0;i<count;i++) {
        int64_t sum=0;for(unsigned k=0;k<3;k++)sum+=(int64_t)g->matrix[8+k]*(int16_t)v[i].v.ob[k];
        if((int32_t)((sum>>16)+g->matrix[11])<=0)return; /* fixed recovery requires positive w */
    }
    struct GeometryAttributes *a=&geometry_attributes[0];memset(a,0,sizeof *a);
    unsigned lights=rsp.current_num_lights-1;
    a->lighting=(rsp.geometry_mode&G_LIGHTING)!=0;
    for(unsigned k=0;k<3;k++) {
        a->coeff[k]=a->lighting&&lights?rsp.current_lights_coeffs[0][k]:0.0f;
        a->words[3]|=(uint32_t)rsp.current_lights[0].col[k]<<(8*k);
        a->words[4]|=(uint32_t)rsp.current_lights[lights].col[k]<<(8*k);
    }
    a->words[5]=rsp.texture_scaling_factor.s|((uint32_t)rsp.texture_scaling_factor.t<<16);
    uint32_t slots=count==32?0xffffffffu:((1u<<count)-1u)<<first;
    geometry_pending=geometry_attribute_pending=slots;
    uint32_t expected_depth[32];
    for(unsigned i=0;i<count;i++) {
        struct GeometryVertex *gv=&geometry_vertices[first+i];memset(gv,0,sizeof *gv);
        for(unsigned k=0;k<3;k++)gv->pos[k]=(int16_t)v[i].v.ob[k];
        memcpy(gv->tc,v[i].v.tc,sizeof gv->tc);memcpy(&gv->cn,v[i].v.cn,4);
        gv->fog=configEnableFog&&(rsp.geometry_mode&G_FOG);gv->fog_mul=rsp.fog_mul;gv->fog_offset=rsp.fog_offset;
        geometry_materialize(first+i);expected_depth[first+i]=geometry_depth[first+i];
    }
''')
    tail=tail.replace('    gfx_mesh_lazy_save(count,first,v,aspect);','''    gfx_mesh_lazy_save(count,first,v,aspect);
    mesh_lazy_depth_valid=0;mesh_lazy_fixed=true;memcpy(mesh_lazy_matrix,g->matrix,sizeof mesh_lazy_matrix);''')
    tail=tail.replace('    if(memcmp(&rsp,&current,offsetof(struct RSP,loaded_vertices)))fail("current state",count,0);', '''    for(unsigned i=first;i<first+count;i++)if(original_pending&first_mask&(1u<<i)) {
        if(!(mesh_lazy_depth_valid&(1u<<i))||geometry_depth[i]!=expected_depth[i])fail("partial fixed depth",count,i);
    }
    if(mesh_lazy_depth_valid!=(original_pending&first_mask))fail("partial depth ownership",count,0);
    if(memcmp(&rsp,&current,offsetof(struct RSP,loaded_vertices)))fail("current state",count,0);''')
    tail=tail.replace('    if(mesh_lazy_pending)fail("pending",count,0);','''    if(mesh_lazy_pending)fail("pending",count,0);
    if(mesh_lazy_depth_valid!=(original_pending&~overwrite))fail("reload depth ownership",count,0);''')
    tail=tail.replace('    gfx_mesh_cpu_vertices(count,first,v,aspect,&rsp,mesh_lazy_fog,mesh_lazy_epoch);','    gfx_mesh_fixed_vertices(first,count);')
    tail=tail.replace('rsp.current_num_lights=random32()%3+1;','rsp.current_num_lights=random32()%2+1;')
    tail=tail.replace('    if((rsp.geometry_mode&G_LIGHTING)&&(random32()&1))rsp.geometry_mode|=G_TEXTURE_GEN;','')
    tail=tail.replace('v[i].v.ob[k]=random_float(300.0f);','v[i].v.ob[k]=(int16_t)random_float(300.0f);')
    tail=tail.replace('PASS lazy exact CPU:','PASS lazy original fixed recovery:')
    (dest/'test.c').write_text(prefix+tail)
    for name in ('sm64_lazy_state.h','sm64_lazy_capture.h'):shutil.copy2(HERE/name,dest/name)
    shutil.copy2(control/'sm64_lazy_cpu.h',dest/'sm64_lazy_cpu.h');shutil.copy2(control/'scene.bin',dest/'scene.bin')
    results=[]
    for flags,label in [(['-O3','-ffast-math','-ffp-contract=off'],'optimized'),(['-O1','-fsanitize=address,undefined','-fno-omit-frame-pointer'],'sanitized')]:
        command=['gcc','-std=gnu99','-fno-strict-aliasing','-fwrapv',*flags,str(dest/'test.c'),'-lm','-o',str(dest/label)]
        subprocess.run(command,check=True)
        run=subprocess.run([str(dest/label),str(dest/'scene.bin')],check=True,capture_output=True,text=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
        results.append(dict(configuration=label,output=run.stdout));print(run.stdout,end='')
    result=dict(passed=True,scope=__doc__,results=results,sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.glob('*.h')})
    (dest/'result.json').write_text(json.dumps(result,indent=2)+'\n')

if __name__=='__main__':main()
