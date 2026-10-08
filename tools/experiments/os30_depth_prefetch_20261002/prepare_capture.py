"""Private functional SM64 capture; GPU/CPU time here is NOT a frame-time model.
The SDK raster model is os25-only. It supplies fences/flips; current os30 RTL
replays every command. Framebuffer/depth output must be compared RTL-to-RTL.
Textures use the application's SDRAM fallback (fast-texture capability off).
"""
from pathlib import Path
import shutil,subprocess,json,hashlib
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-depth-prefetch-20261002'
OUT.mkdir(parents=True,exist_ok=True)
SDK=ROOT.parent/'openfpgaSDK/tools/qsim'
q=OUT/'qsim'; q.mkdir(exist_ok=True)
for f in SDK.iterdir():
    if f.suffix in ('.c','.h') or f.name=='Makefile': shutil.copy2(f,q/f.name)
def once(s,a,b):
    assert s.count(a)==1,(a,s.count(a))
    return s.replace(a,b,1)
s=(q/'env.c').read_text()
s=once(s,'static uint64_t clock_read(qenv *E, uint32_t pc)\n{','''static uint64_t clock_read(qenv *E, uint32_t pc)
{
    /* Exactly one fixed game step per presented frame; no FPS inference. */
    const qsim_sym *sound = qsim_syms_lookup(&E->syms, "configEnableSound");
    if (sound) gzero(E, sound->addr, 1);
    return 5000000u + (E->flips * 1000000ull + 29) / 30;
''')
# Advertise the current geometry/colour path, with SDRAM texture allocation.
features=0x01A7E35B
for bit in (15,18,19,21,22,23,25,26,28,30): features &= ~(1<<bit)
for bit in (10,12,20,27,29): features |= 1<<bit
s=once(s,'0x01A7E35Bu',f'0x{features:08x}u')
s=once(s,'    case 94: {', '''    case 92: { /* video timing, functional only */
        gzero(E, a0, 32);
        wr32(E, a0, (uint32_t)(E->flips * 2));
        wr32(E, a0 + 4, (uint32_t)E->flips);
        break;
    }
    case 94: {''')
s=once(s,'        case 0x00: v = 3; break;', '        case 0xD4: v = 100000000u; break;\n        case 0x00: v = 3; break;')
# Deterministic button presses for entering the castle intro. No-input run
# leaves the normal attract sequence, including head and Bowser demos.
s=once(s,'    case 15: gzero(E, a1, 24); break;', '''    case 15:
        gzero(E, a1, 24);
        if (getenv("SM64_INTRO") && a0 == 0)
            wr32(E, a1, (E->flips >= 120 && E->flips < 122) ? (1u << 15)
                : (E->flips >= 200 && E->flips < 202) ? (1u << 6) : 0);
        break;''')
s=once(s,'    case 6: {', '    case 5: if (a0 < 256) E->palette[a0] = a1 & 0xffffff; break;\n    case 6: {')
s=once(s,'off == 0x28 && vec_wanted', '(off == 0x28 || off == 0x04) && vec_wanted')
(q/'env.c').write_text(s)
(OUT/'sm64.app').write_text('''name = SM64 capture
elf_name = sm64.elf
frame_symbol = gGlobalTimer
slot = 1 os.bin stub:135400
slot = 3 sm64.elf stub:876096
slot = 7 bank.ofsf data:bank.ofsf
slot = 10 sm64.sav nv
''')
with (OUT/'qsim-build.log').open('w') as log:
    subprocess.run(['make','-j3'],cwd=q,stdout=log,stderr=subprocess.STDOUT,check=True)
(OUT/'capture-inputs.json').write_text(json.dumps({'scope':__doc__,'features':hex(features),'inputs':{
    str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in [*q.glob('*.c'),*q.glob('*.h'),ROOT.parent/'SM64/.obj/sm64/app.elf'] }},indent=2)+'\n')
print(q/'qsim')
