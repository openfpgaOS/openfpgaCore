#!/usr/bin/env python3
"""One fixed seed-2 fit of measured MAC-stream+CB4; immutable v4 input ancestry."""
from pathlib import Path
import hashlib,importlib.util,json,shutil,subprocess,time
ROOT=Path(__file__).resolve().parents[4]
HERE=Path(__file__).resolve().parent
PACKAGE=ROOT/"tools/experiments/quake2_native_mesh_20261012/core-v4"
BASE=Path("/tmp/quake2-native-mesh-20261012/fit-silent-v4")
OUT=Path("/tmp/q2-native-followup-20261012/fit-mac-cb4")
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
manifest=json.loads((PACKAGE/"manifest.json").read_text())
spec=importlib.util.spec_from_file_location("v4package",PACKAGE/"package.py");pkg=importlib.util.module_from_spec(spec);spec.loader.exec_module(pkg)
qsf_name="src/fpga/targets/pocket/ap_core.qsf"
original_qsf=next(data for name,data,mode in pkg.source_members(manifest) if name==qsf_name)
original_qsf=original_qsf.replace(b"set_global_assignment -name VERILOG_MACRO INCLUDE_HW_MIXER\n",b"set_global_assignment -name VERILOG_MACRO NATIVE_MESH_SILENT_AUDIO\n")
assert hashlib.sha256(original_qsf).hexdigest()==manifest["fit_input_sha256"][qsf_name]
assert not OUT.exists()
OUT.mkdir(parents=True)
for name,digest in manifest["fit_input_sha256"].items():
    dest=OUT/name;dest.parent.mkdir(parents=True,exist_ok=True)
    if name==qsf_name:dest.write_bytes(original_qsf)
    else:assert sha(BASE/name)==digest,name;shutil.copy2(BASE/name,dest)
    assert sha(dest)==digest,name
worker=HERE/"native-mac-stream-v1/source/gpu_geometry_worker.v"
assert sha(worker)=="56b3f89d26ef2ffa3588c5679cfb9806d3859071f27fe6e30bdabcd06bcabfab"
shutil.copy2(worker,OUT/"src/fpga/common/gpu_geometry_worker.v")
qsf=OUT/qsf_name;s=qsf.read_text();line="set_global_assignment -name VERILOG_MACRO INCLUDE_CB_WINDOW2\n";assert s.count(line)==1
qsf.write_text(s.replace(line,"# Color blend destination read window: use existing four-word default.\n"))
assert "set_global_assignment -name SEED 2" in s
pins={n:sha(OUT/n) for n in manifest["fit_input_sha256"]}
changes={n:dict(before=h,after=pins[n]) for n,h in manifest["fit_input_sha256"].items() if h!=pins[n]}
assert set(changes)=={qsf_name,"src/fpga/common/gpu_geometry_worker.v"}
receipt=dict(baseline_manifest_sha256=sha(PACKAGE/"manifest.json"),source_sha256=pins,changes=changes,recipe_sha256=sha(Path(__file__)),seed=2,silent_audio=True,mixer=False,configured_mhz=100,deployed=False,timing_passed=False,commands=[],returncodes={})
(OUT/"source-pins.json").write_text(json.dumps(pins,indent=2)+"\n")
def save():(OUT/"build-receipt.json").write_text(json.dumps(receipt,indent=2)+"\n")
Q=Path("/home/alberto/altera_lite/25.1std/quartus/bin")
receipt["quartus_version"]=subprocess.check_output([str(Q/"quartus_sh"),"--version"],text=True).strip();assert "25.1std.0 Build 1129" in receipt["quartus_version"]
save();cwd=OUT/"src/fpga/targets/pocket"
steps=[(p,[str(Q/("quartus_"+p)),"ap_core"]) for p in ("map","fit","asm","sta")]
steps.append(("corners",[str(Q/"quartus_sta"),"-t",str(OUT/"tools/report_core_timing.tcl"),"ap_core",str(OUT/"timing")]))
for phase,cmd in steps:
    print("Starting",phase,flush=True);t=time.monotonic();entry=dict(phase=phase,argv=cmd,cwd=str(cwd));receipt["commands"].append(entry);save()
    with (OUT/(phase+".log")).open("w") as log:r=subprocess.run(cmd,cwd=cwd,stdout=log,stderr=subprocess.STDOUT)
    entry["elapsed_seconds"]=time.monotonic()-t;receipt["returncodes"][phase]=r.returncode;save()
    if phase=="asm" and r.returncode==0:
        table=bytes(int(f"{i:08b}"[::-1],2) for i in range(256));artifact=OUT/"os30-native-mac-cb4.rbf_r";artifact.write_bytes((cwd/"output_files/ap_core.rbf").read_bytes().translate(table));receipt["bitstream_sha256"]=sha(artifact);save()
    if r.returncode and phase!="corners":raise SystemExit(r.returncode)
receipt["timing_passed"]=receipt["returncodes"]["corners"]==0
receipt["post_build_source_changes"]={n:sha(OUT/n) for n,h in pins.items() if sha(OUT/n)!=h};save()
print(json.dumps(dict(output=str(OUT),returncodes=receipt["returncodes"],timing_passed=receipt["timing_passed"])),flush=True)
