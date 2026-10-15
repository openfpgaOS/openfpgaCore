#!/usr/bin/env python3
"""Prepare AJ02 snapshot v2 as one isolated seed3/90MHz physical project. NEVER compiles."""
import argparse,hashlib,json,re,shutil
from pathlib import Path
HERE=Path(__file__).resolve().parent
A=HERE.parent
P=A.parent
ROOT=P.parents[2]
PHYSICAL=P/'work/core/docs/measurements/psram32-build-upload-reset-placeeffort-v1'
BASE=P/'cached-cpu-v1/native-sdram-v2'
ACTIVE=['gpu_core.v','phase10_load_alias.v','geometry_raw_load.v','gpu_mesh_job_front.v','geometry_async_mesh.v','geometry_float_cull_mem.v','geometry_import_convert.v','geometry_mesh_command_fetch.v','geometry_mesh_packet.v','geometry_mesh_raw_frontend.v','geometry_mesh_stream.v','geometry_mesh_task_ctrl.v','geometry_mesh_task_engine.v']
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def top_wiring(text):
    ports=(A/'design/ports.vh').read_text()
    declarations=[];names=[]
    for line in ports.splitlines():
        if not re.match(r'^(input|output) wire ',line):continue
        declarations.append(re.sub(r'^(input|output) wire ', 'wire ',line).rstrip(',')+';')
        names+=re.findall(r'\bmesh_\w+',line)
    assert len(names)==len(set(names))==24,names
    nets='`ifdef INCLUDE_GPU_ASYNC_MESH\n'+'\n'.join(declarations)+'\n`endif\n'
    links='`ifdef INCLUDE_GPU_ASYNC_MESH\n'+''.join(f'        .{n}({n}),\n'for n in names)+'`endif\n'
    assert text.count('    geometry_load_alias geometry_load (')==1
    text=text.replace('    geometry_load_alias geometry_load (',nets+'    geometry_load_alias geometry_load (\n'+links)
    assert text.count(') gpu (\n')==1
    text=text.replace(') gpu (\n',') gpu (\n'+links)
    return text

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--native',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);arg=ap.parse_args()
    native=arg.native.resolve();out=arg.output.resolve();assert out.is_relative_to(HERE)and not out.exists(),out
    prepared=json.loads((native/'prepared.json').read_text());baseline=json.loads((BASE/'prepared.json').read_text())
    for n,h in prepared['source_sha256'].items():assert sha(native/n)==h,n
    macro=lambda x:{a for a in x['command']if a.startswith('-D')}
    assert macro(prepared)-macro(baseline)=={'-DINCLUDE_GPU_ASYNC_MESH'}
    assert not macro(baseline)-macro(prepared)
    frozen=out/'frozen';shutil.copytree(PHYSICAL/'frozen',frozen)
    build=out/'build';build.mkdir();shutil.copy2(PHYSICAL/'build/ap_core.qpf',build/'ap_core.qpf');shutil.copy2(PHYSICAL/'os.bin',out/'os.bin')
    qsf=(PHYSICAL/'build/ap_core.qsf').read_text().replace(str(PHYSICAL/'frozen'),str(frozen))
    for setting,wanted in [('SEED','3'),('PLACEMENT_EFFORT_MULTIPLIER','3.0')]:
        assert re.findall(r'^set_global_assignment -name '+setting+r' (.+)$',qsf,re.M)==[wanted]
    assert 'VERILOG_MACRO INCLUDE_CLK90' in qsf
    qsf+='\nset_global_assignment -name VERILOG_MACRO INCLUDE_GPU_ASYNC_MESH\n'
    changed={};top=frozen/'src/fpga/targets/pocket/os30/core_top.v';old=sha(top)
    top.write_text(top_wiring(top.read_text()));changed[str(top.relative_to(frozen))]={'previous_sha256':old,'sha256':sha(top),'generator_sha256':sha(Path(__file__))}
    for name in ACTIVE:
        source=native/'frozen/src/fpga/common'/name;assert source.is_file(),source
        target=frozen/'src/fpga/common/os30'/name
        prior=BASE/'frozen/src/fpga/common'/name
        if not target.exists()and prior.exists():target=frozen/'src/fpga/common'/name
        existed=target.exists();previous=sha(target)if existed else None
        if existed:assert prior.is_file()and previous==sha(prior),(name,'physical/native baseline mismatch')
        shutil.copy2(source,target);changed[str(target.relative_to(frozen))]={'native_source':str(source),'sha256':sha(source),'previous_sha256':previous}
        if not existed:qsf+=f'\nset_global_assignment -name VERILOG_FILE {target}\n'
    qsf+=f'\nset_global_assignment -name SEARCH_PATH {frozen/"src/fpga/common/os30"}\n'
    for line in qsf.splitlines():
        m=re.match(r'set_global_assignment -name (\w+_FILE) (/.+)$',line)
        if m:
            p=Path(m[2]).resolve();assert p.is_relative_to(frozen)and p.is_file(),(line,p)
    constraints={}
    for p in (PHYSICAL/'frozen').rglob('*.sdc'):
        rel=p.relative_to(PHYSICAL/'frozen');assert sha(p)==sha(frozen/rel);constraints[str(rel)]=sha(p)
    (build/'ap_core.qsf').write_text(qsf)
    assert not (build/'db').exists()and not(build/'incremental_db').exists()
    rec={'status':'PREPARED_ONLY_NOT_COMPILED','native':str(native),'native_prepared_sha256':sha(native/'prepared.json'),'physical_baseline':str(PHYSICAL),'physical_baseline_inputs_sha256':sha(PHYSICAL/'inputs.json'),'qualification_sha256':sha(PHYSICAL/'timing-qualification.json'),'configured_mhz':90,'seed':3,'placement_effort':3.0,'changed_rtl':changed,'constraints_sha256':constraints,'qsf_sha256':sha(build/'ap_core.qsf'),'source_sha256':{str(p.relative_to(frozen)):sha(p)for p in sorted(frozen.rglob('*'))if p.is_file()},'os_sha256':sha(out/'os.bin'),'recipe_sha256':sha(Path(__file__)),'compile_command_after_explicit_parent_authorization':['bash',str(ROOT/'tools/quartus-container.sh'),str(build)],'quartus_runner_sha256':sha(ROOT/'tools/quartus-container.sh'),'limits':['No existing database/checkpoint copied','No seed sweep','No clock or timing-exception changes','No compile or deployment performed','Native correctness and meaningful whole-frame gain required before single fit','Expected +3M10K is a source-level estimate; ALM and timing remain unqualified']}
    (out/'inputs.json').write_text(json.dumps(rec,indent=2)+'\n');print(json.dumps({'status':rec['status'],'output':str(out),'changed_inputs':len(changed),'input_sha256':sha(out/'inputs.json')},indent=2))
if __name__=='__main__':main()
