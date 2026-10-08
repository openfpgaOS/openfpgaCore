#!/usr/bin/env python3
"""Export independent worker vertex-format cases without changing sealed proof."""
import json
from pathlib import Path
import subprocess
from phase10_experiment import CAMP,ROOT,sha,write
HERE=Path(__file__).resolve().parent
OUT=CAMP/'phase10/packet-vectors-v1';BASE=CAMP/'phase10/run-consumer-v1'
def main():
    OUT.mkdir(exist_ok=False)
    s=(BASE/'phase10_run_reference.cpp').read_text()
    s=s.replace('struct Vertex {','static std::ofstream cases;\nstatic uint64_t case_count=0;\nstruct Vertex {')
    old='  return {quant(v.x*256.f),quant(v.y*256.f),quant(v.w*256.f),quant(tu*65536.f),quant(tv*65536.f),(r>>3)<<11|(g>>2)<<5|(b>>3),quant(depth)};'
    new='''  std::array<uint32_t,7> result={quant(v.x*256.f),quant(v.y*256.f),quant(v.w*256.f),quant(tu*65536.f),quant(tv*65536.f),(r>>3)<<11|(g>>2)<<5|(b>>3),quant(depth)};
  uint32_t input[15]={slot,draw[1],draw[3],draw[4],draw[5],draw[6],draw[7],u(v.x),u(v.y),u(v.z),u(v.w),u(v.inv),uint32_t(uint16_t(int16_t(v.tu)))|(uint32_t(uint16_t(int16_t(v.tv)))<<16),v.rgba,v.clip};
  cases.write((char*)input,sizeof input);cases.write((char*)result.data(),28);case_count++;
  return result;'''
    assert s.count(old)==1;s=s.replace(old,new)
    s=s.replace(' if(argc!=2)return 2;',' if(argc!=3)return 2;cases.open(argv[2],std::ios::binary);')
    s=s.replace('  need(!r.pending,"unterminated RUN");','  need(!r.pending,"unterminated RUN");cases.close();std::cerr<<"exported "<<case_count<<" packets\\n";')
    cpp=OUT/'export.cpp';cpp.write_text(s);exe=OUT/'export';binary=OUT/'cases.bin'
    subprocess.run(['g++','-O2','-std=c++17','-mfma','-ffp-contract=off',str(cpp),'-o',str(exe)],check=True)
    with (OUT/'run.log').open('x') as log:subprocess.run([str(exe),str(BASE/'worker-input.bin'),str(binary)],stdout=log,stderr=subprocess.STDOUT,check=True)
    write(OUT/'manifest.json',dict(source_sha256=sha(cpp),worker_sha256=sha(BASE/'phase10_run_reference.cpp'),worker_input_sha256=sha(BASE/'worker-input.bin'),binary_sha256=sha(binary),records=binary.stat().st_size//88,record_words=22,helper_sha256=sha(Path(__file__)),scope='Worker-derived qsim raw LOAD results + real DRAW uniforms + exact independently generated seven payload fields. Header and slot are supplied by emitter. All CPU backend prefix checks rerun.'))
    print((OUT/'run.log').read_text())
if __name__=='__main__':main()
