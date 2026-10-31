"""Local-only helpers for reproducible audio qualification."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
ROOT = HERE.parents[3]

def fixture_path(name):
    refs = json.loads((HERE / "references.json").read_text())
    return ROOT / refs[name]["path"] if name in refs else FIXTURES / name

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Unexpected source structure: " + repr(old))
    return text.replace(old, new)

def verify_fixtures():
    pins = json.loads((HERE / "fixtures.json").read_text())
    pins.update(json.loads((HERE / "references.json").read_text()))
    for name, pin in pins.items():
        if sha(fixture_path(name)) != pin["sha256"]:
            raise ValueError("Fixture changed: " + name)
    return pins

def new_output(path):
    path = path.absolute()
    for parent in [path, *path.parents]:
        if parent.is_symlink():
            raise ValueError("Output or ancestor is a symlink: " + str(parent))
    path.mkdir(parents=True, exist_ok=False)
    return path.resolve()

def stage(out, names, mixer=None):
    out.mkdir()
    for name in names:
        shutil.copyfile(fixture_path(name), out / name)
    if mixer is not None:
        (out / "audio_mixer.v").write_text(mixer)
    return out

def mixer_variant(variant):
    text = (FIXTURES / "audio_mixer.v").read_text()
    old = '(* ramstyle = "MLAB" *) reg [21:0] pos_latch [0:31];'
    if variant in ("ram", "combined"):
        text = once(text, old, old.replace("MLAB", "M10K"))
    if variant in ("bypass", "combined"):
        text = "\x60define INCLUDE_NONSTREAM_BYPASS\n" + text
    return text

def invoke(command, log, env=None, check=True):
    save(log.with_suffix(".command.json"), list(map(str, command)))
    with log.open("w") as stream:
        return subprocess.run(command, cwd=log.parent, stdout=stream,
                              stderr=subprocess.STDOUT, env=env,
                              check=check, timeout=600)

def build(out, source, top, names, flags=(), jobs=2):
    command = ["verilator", "--cc", "--exe", "--build", "-j", str(jobs),
               "-Wno-fatal", "-Wno-BADVLTPRAGMA", "--top-module", top,
               "--Mdir", str(out / "obj"), "-I" + str(source),
               "-CFLAGS", "-std=c++17 -O2", *flags,
               *[str(source / name) for name in names]]
    invoke(command, out / "build.log")
    return out / "obj" / ("V" + top)
