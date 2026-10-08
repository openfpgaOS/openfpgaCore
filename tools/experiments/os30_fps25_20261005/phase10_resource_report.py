#!/usr/bin/env python3
"""Seal the read-only retained A resource audit and concrete tradeoff evidence."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase10/resource-audit'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
    with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')

def main():
    spec=importlib.util.spec_from_file_location('vec',Path(__file__).with_name('phase8_vectors.py'));v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
    rows=[];evidence={}
    for p in sorted((ROOT/'docs/measurements/os30-fps25-20261005/phase9/checks-state-f11/capture-normal/gpuvec').glob('*.vec')):
        d=v.parse(p.read_bytes());writes=[struct.unpack('<II',t[4:]) for t in d['tokens'] if t[:4]==b'MMIO']
        route=[value for reg,value in writes if reg==0x38]
        assert route==[1]
        rows.append(dict(frame=int(p.stem.split('_')[1]),route_writes=route,texture_preload_bytes=sum(t['bytes'] for t in d['textures']),texture_upload_words=sum(reg==0x2c for reg,value in writes)))
        evidence[str(p.relative_to(ROOT))]=sha(p)
    save(OUT/'active-texture-route.json',dict(passed=True,frames=rows,evidence_sha256=evidence,scope='Captured normal F11 vectors all select physical CRAM1 texture route. Dedicated memory is not unused.'))
    report='''Retained os30 A resource audit (read-only, 2026-10-06)

The deployed-A raw/Pocket bitstream identity is verified, including exact bit
reversal and all 104 frozen source hashes. The saved seed-2 fitter reports
17,248 ALMs needed, 18,041 physically placed, 1,846/1,848 LABs and 306/308 M10Ks.
DSP use is 42/66. Only two complete LABs are unused; 1,232 ALMs of arithmetic
capacity is not 1,232 readily placeable ALMs. Packing difficulty is High.
The 100 MHz design estimates 92.53 MHz (slow85) / 93.65 MHz (slow0); its setup
slack is -0.842 ns. This is the retained core, not a newly closed 100 MHz fit.

Physical RAM ownership is counted by unique M10K locations (306), avoiding
logical arrays whose reported blocks share the same location. CPU subsystem
uses 182 M10Ks: 134 data-cache-side, 29 instruction-cache-side, 19 predictors,
register files/CSR/store-buffer. GPU core uses 52: ring16, texture18, vertex5,
reciprocal2, triangle/span5, blend2, framebuffer queue1, texture metadata3.
CPU peripheral BRAM/UART uses33; render cache10; audio8; scanout9; bridge10;
SDRAM arbitration2. Exact rows are in ram-owners.json and entities.json.

Already removed: GPU matrix MAC, GPU lighting, standalone clip triangles,
command DMA, parameter spans, palette path, and original coordinate arrays.
The old three coordinate M10Ks are already absent from retained A. Current
indexed vertex cache/projection and RGB/alpha/combine paths are used by games.
A further blanket XFORM or combiner cut would remove active rendering behavior.

The dedicated CRAM1 texture chip is active, not a free 527-ALM cut. All eight
F11 captures write texture route=1; resident snapshots span 8,768 to264,512
bytes. Current Quake2 switches per material to its fast-memory domain. Its
no-fast-memory fallback reduces 3D texture width and height fourfold and can
fall back to white when its smaller budget is exhausted. The historical
private mesh prototype's 527-ALM notex saving does not establish a lossless
cut in the current games. active-texture-route.json binds actual F11 streams.

The 4096-word CPU ring is nearly full in all eight existing F11 coupled runs:
peaks4094/4095 words. Halving it to2048 would free8 M10Ks, but current traffic
uses that space and would encounter additional backpressure. Both SDKs also
hard-code16384 bytes /4095-word max batches. A smaller ring needs a versioned
size contract, updated chunking/wrap checks, and performance qualification.
It cannot simply be changed in RTL under existing app binaries.

The 32 KiB CPU BRAM offers a future ownership opportunity, not a proven cut.
Current F11 and Quake2 app PT_LOADs are all in SDRAM; APP_BRAM0x4000..0x7800
is unused by those ELFs. However, the OS loader supports that application
region and the boot stack descends from0x8000; the linker reserves at least
8 KiB stack headroom. An explicitly lent0x4000..0x6000 bank (8 M10Ks) after
boot could preserve low16 KiB OS and top8 KiB boot stack, but stack bounds,
loader ownership/capability, RAM banking/ports and timing must be proven.
The current CPU access path is uncached peripheral AXI, not zero-latency TCM.

Current linked libraries require LR/SC: F11 normal and diagnostic contain16
LR.W and16 SC.W instructions each, Quake2 contains22+22, and retained OS12+12.
These include malloc/free paths, so single-threaded operation does not make
A unused. No arithmetic AMO instructions were found in decoded function
ranges in these four ELFs. The generator has only --with-rva, coupling LR/SC
and AMO. Splitting arithmetic AMO from LR/SC is a new custom-ISA experiment,
not an existing safe flag; its area saving is unmeasured, ISA reporting and
unsupported-app handling would need an explicit policy. See atomics/result.

Prior actual whole-core trials do not support easy300-600ALM savings: halving
D-cache128->64 KiB freed66 M10Ks but only8 ALMs in that trial; moving texture
queues to BRAM improved another17 ALMs while using5 M10Ks. A serial mixer
trial used54 more ALMs than the pipelined comparator. These are historical
configurations, useful directionally but not additive to retained A.
WQ16->8, early-Z restart, and parallel scale are performance features with no
isolated current-A area proof. A historical+119ALM bundle included WQ depth,
clamp flags and Z predecode, so119 cannot be attributed to WQ alone.

The promising immediate RAM lever is packing the 32x176-bit vertex cache as
192x32, retaining all32 slots/176 bits. It uses six serial words per vertex,
so the design target is four fewer M10Ks with5 added cycles per load and15
per indexed triangle. A separate private vcache-narrow-v1 experiment now
checks actual pixels and full-core resource/timing; this audit does not
assume its expected RAM/logic cost before fitting.

For raw-frontend ALM estimates, use whole-core integration before pruning
active features. The isolated transform-light-v2 fitter includes228 ALMs
reported unavailable because456 virtual pins were reserved; that component
is not directly additive once integrated. Diagnostic counters not connected
to real outputs may also be eliminated. No unused300-600ALM block or fitted
whole-core accelerator is established by this audit. The concrete next step
is the narrow-cache proof plus real integration, preserving CRAM1, ring size,
image quality and current CPU ISA in that first measured build.
'''
    (OUT/'REPORT.txt').write_text(report)
    external={}
    names=['tools/experiments/os30_worker_20261003/README.txt','tools/experiments/os30_frontend_20261004/README.txt','tools/experiments/os30_mesh_20261004/README.txt','docs/measurements/os30-feature-audit-20261003/features.csv','docs/measurements/os30-feature-audit-20261003/gpu-screen.json','tools/experiments/os30_fps25_20261005/phase10_resource_audit.py','tools/experiments/os30_fps25_20261005/phase10_atomic_audit.py','tools/experiments/os30_fps25_20261005/phase10_resource_report.py','tools/experiments/os30_fps25_20261005/phase8_vectors.py']
    for n in names:
        p=ROOT/n;external[n]=sha(p)
    for p in sorted(OUT.rglob('*')):
        if p.is_file():external[str(p.relative_to(ROOT))]=sha(p)
    # Recheck every external input bound by the retained resource / atomic audit.
    for result in [OUT/'result.json',OUT/'atomics/result.json',OUT/'active-texture-route.json']:
        for key,h in json.loads(result.read_text())['evidence_sha256'].items():
            p=Path(key) if key.startswith('/') else ROOT/key
            assert sha(p)==h,p
    save(OUT/'final-manifest.json',dict(passed=True,scope='Read-only resource evidence; no production edits/qualification',evidence_sha256=external))
    print(json.dumps(dict(passed=True,evidence_files=len(external),manifest_sha256=sha(OUT/'final-manifest.json'))))

if __name__=='__main__':main()
