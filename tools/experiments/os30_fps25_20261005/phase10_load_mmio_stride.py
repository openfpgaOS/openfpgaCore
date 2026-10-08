#!/usr/bin/env python3
"""Stage LOD3: the same MMIO with9word DMA bursts into exclusive64B slots."""
import difflib,hashlib,json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'raw-load-mmio-v3';BASE=P/'raw-load-mmio-v2'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def main():
 OUT.mkdir(exist_ok=False);F=OUT/'frozen';shutil.copytree(BASE/'frozen',F)
 old=json.loads((BASE/'prepared.json').read_text())['source_sha256']
 for p,h in old.items():assert sha(F/p)==h,p
 writer=HERE/'phase10_loaded_vertex_writer_stride.v';wp=F/'src/fpga/common/geometry_loaded_vertex_writer.v';shutil.copy2(writer,wp)
 mp=F/'src/fpga/common/geometry_load_mmio.v';s=mp.read_text()
 assert s.count('geometry_loaded_vertex_writer writer(')==1;s=s.replace('geometry_loaded_vertex_writer writer(','geometry_loaded_vertex_writer #(.OUTPUT_STRIDE64(1)) writer(')
 assert s.count("32'h4c4f4402")==1;s=s.replace("32'h4c4f4402","32'h4c4f4403").replace('private interface version2','private interface version3')
 s=s.replace('MMIO v2: original24B vertices ->36B LoadedVertex DMA.','MMIO v3: original24B vertices ->36B data in exclusive64B slots.')
 mp.write_text(s)
 hashes={str(p.relative_to(F)):sha(p) for p in F.rglob('*') if p.is_file()}
 changes={p:dict(before=old.get(p),after=h) for p,h in hashes.items() if old.get(p)!=h}
 assert sorted(changes)==['src/fpga/common/geometry_load_mmio.v','src/fpga/common/geometry_loaded_vertex_writer.v']
 save(OUT/'prepared.json',dict(private_only=True,source_sha256=hashes,changed_from_v2=changes,parent_prepared_sha256=sha(BASE/'prepared.json'),writer_source_sha256=sha(writer),recipe_sha256=sha(Path(__file__)),scope=__doc__,contract='OUTPUT_STRIDE64 fixed1; outputbase points to firstactualtargetslot, raw dest indexes arithmetic cache only. Every36byte record uses one9word AW, nextrecord+64. Count complete64byte slots must fit SDRAM aperture. Padding is untouched. CPUpreinvalidates only exclusive overwritten slots; noaccessuntilcompleted+success, theninvalidatebeforeuse. ID3 prevents packed36 driver confusion.'))
 patch=[]
 for name in changes:
  patch.extend(difflib.unified_diff((BASE/'frozen'/name).read_text().splitlines(True),(F/name).read_text().splitlines(True),fromfile='a/'+name,tofile='b/'+name))
 (OUT/'candidate.patch').write_text(''.join(patch))
 regs=json.loads((BASE/'registers.json').read_text());regs['id']='0x4C4F4403';regs['registers']['040']='RW result_base64aligned, points to firsttargetslot;9wordswritten per64byte stride;count fullslots mustfit10000000..14000000'
 save(OUT/'registers.json',regs);print(OUT)
if __name__=='__main__':
 if sys.argv[1:]==['prepare']:main()
 else:raise SystemExit('prepare')

