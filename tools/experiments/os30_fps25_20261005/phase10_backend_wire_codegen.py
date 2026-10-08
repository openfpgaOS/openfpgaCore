#!/usr/bin/env python3
"""Bounded native-code check of diagnostic gating, with original compiler flags."""
import json, os, shlex, shutil, subprocess
from pathlib import Path
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection
from phase10_experiment import CAMP, ROOT, sha, write

P=CAMP/'phase10';OUT=P/'backend-wire-counters-v1';OLD=P/'load-submit-overlap-v1'
DEST=OUT/'codegen';STAGE=OUT/'SM64';CWD=STAGE/'src/sm64'
COUNTERS=['g_vc_words','g_vc_loads','g_vc_tris_cached','g_vc_tris_legacy']
def symbols(path):
 with path.open('rb')as f:return {s.name:(int(s['st_value']),int(s['st_size']))for s in ELFFile(f).get_section_by_name('.symtab').iter_symbols()}
def code(path):
 with path.open('rb')as f:
  e=ELFFile(f);sections={s.name:s.data().hex()for s in e.iter_sections()if int(s['sh_flags'])&4}
  reloc={}
  for s in e.iter_sections():
   if not isinstance(s,RelocationSection):continue
   target=e.get_section(s['sh_info'])
   if not int(target['sh_flags'])&4:continue
   syms=e.get_section(s['sh_link']);reloc[s.name]=[(int(r['r_offset']),int(r['r_info_type']),syms.get_symbol(r['r_info_sym']).name,int(r['r_addend']))for r in s.iter_relocations()]
  return dict(executable_sections=sections,relocations=reloc)
def main():
 DEST.mkdir(exist_ok=False)
 lines=(OUT/'build-normal.log').read_text().splitlines()
 line=next(x for x in lines if ' -c -o 'in x and x.endswith(' sm64/src/pc/gfx/gfx_gpu.c'))
 args=shlex.split(line);assert '-flto'not in args
 args[args.index('-o')+1]='OUTPUT';args[-1]='INPUT'
 image=subprocess.check_output(['docker','image','inspect','--format={{.Id}}','openfpgaos-firmware'],text=True).strip()
 results=[]
 for flags in ((1,0),(0,1)):
  pair=[]
  for label,parent in [('parent',OLD),('candidate',OUT)]:
   src=DEST/(label+'-gfx_gpu.c')
   if not src.exists():shutil.copy2(parent/'gfx_gpu.c',src)
   obj=DEST/f'{label}-{flags[0]}{flags[1]}.o'
   cmd=[x.replace('-DSM64_PROFILE=0',f'-DSM64_PROFILE={flags[0]}').replace('-DSM64_PERF_DIAG=0',f'-DSM64_PERF_DIAG={flags[1]}')for x in args]
   cmd=[str(src)if x=='INPUT'else str(obj)if x=='OUTPUT'else x for x in cmd]
   cmd[1:1]=['-I'+str(CWD/'sm64/src/pc/gfx')]
   invocation=['docker','run','--rm','--user',f'{os.getuid()}:{os.getgid()}','-v',f'{ROOT}:{ROOT}:ro','-v',f'{DEST}:{DEST}','-e','CPATH=','-w',str(CWD),image]+cmd
   with(DEST/(obj.stem+'.log')).open('x')as f:subprocess.run(invocation,stdout=f,stderr=subprocess.STDOUT,check=True)
   assert all(n in symbols(obj)for n in COUNTERS)
   pair.append(code(obj))
  assert pair[0]==pair[1],flags
  results.append(dict(profile=flags[0],diagnostic=flags[1],native_executable_sections_and_relocations_exact=True,sections=len(pair[0]['executable_sections'])))
 obj=STAGE/'.obj/sm64/sm64/src/pc/gfx/gfx_gpu.o'
 for p in (obj,OUT/'app.elf'):
  s=symbols(p);assert not any(n in s for n in COUNTERS);assert'g_vc_kick'in s
 asm=subprocess.check_output(['llvm-objdump','-dr','--mattr=+m,+a,+f,+c','--no-show-raw-insn',str(obj)],text=True)
 assert not any(n in asm for n in COUNTERS)
 (DEST/'normal-gfx_gpu.asm').write_text(asm)
 getter=asm[asm.index('<gpu_vc_prof_get>:'):].split('\nDisassembly of section',1)[0]
 assert getter.count('sw\tzero,')==4 and 'ret'in getter
 write(DEST/'result.json',dict(passed=True,enabled=results,off_counter_symbols_and_references_absent=True,off_getter_four_zero_stores=True,kick_retained=True,no_lto=True,image=image,normal_object_sha256=sha(obj),elf_sha256=sha(OUT/'app.elf'),helper_sha256=sha(Path(__file__))))
 print(json.dumps(results))
if __name__=='__main__':main()
