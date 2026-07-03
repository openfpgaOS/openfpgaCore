# openfpgaOS — MiSTer distribution (Downloader DB)

Draft distribution design for shipping openfpgaOS + the Doom engine to MiSTer users
through the standard **Downloader / `update_all`** mechanism (custom database), instead of
hand-copying files.

Status: **proposal / scaffold** — the workflow and `setup.sh` here are drafts to build a PR
on. Nothing here is wired into a live release yet.

---

## 1. The core constraint: engine is free, game data is not

| Layer | Distributable? | Delivery |
|---|---|---|
| Core bitstream `openfpgaOS.rbf` | ✅ our code (Apache) | Downloader DB `files` |
| `boot.rom` (os.bin) | ✅ our code | Downloader DB `files` |
| Doom engine `app.elf` | ✅ our code (chocolate-doom GPL) | inside a shipped vhd shell |
| **Commercial IWADs** (doom2/plutonia/tnt/doom/ultimatedoom.wad) | ❌ copyrighted | **user provides** → `setup.sh` injects |
| **PCM music** (DOOMMUS/DOOM2MUS.wad) | ❌ derived from IWAD | **user builds** → `setup.sh` |
| Freeware mods/TCs (Freedoom, REKKR, SIGIL, D4V, …) | ✅ freely licensed | DB `archives` and/or `external_files` (archive.org) |

So the shippable payload is the **engine + freeware instance shells**; the user's own
IWADs are merged in on-device by `setup.sh` (run once from the MiSTer *Scripts* menu).

## 2. On-SD layout the DB installs

Paths are relative to `/media/fat` (Downloader path rules: relative, no `..`, not
`/linux`, `/saves`, `MiSTer`, `menu.rbf`, `MiSTer.ini`).

```
_Console/openfpgaOS.rbf                         core bitstream        (DB file)
games/openfpgaOS/boot.rom                       os.bin                (DB file, reboot:true)
games/openfpgaOS/boot.vhd                       default self-contained image (freeware) (DB, via archive)
games/openfpgaOS/vhd/<instance>.vhd             freeware instance shells (DB, via archive)
games/openfpgaOS/wads/                           <- USER drops their IWADs here (folder only)
Scripts/openfpgaOS_setup.sh                     the wad-injection/build script (DB file)
```

The commercial images — `vhd/doom.vhd` (family library, holds the IWADs) and
`vhd/doommusic.vhd` (PCM music) — are **not** shipped. `setup.sh` builds them on-device
from the wads the user dropped in `games/openfpgaOS/wads/`.

## 3. Downloader database JSON

`db_id` must match the `[section]` in the user's `downloader.ini`. Small stuff
(rbf/os.bin/script) rides as plain `files`; the vhd shells (tens of MB) ride as a **zip
archive** so the downloader pulls one release asset and extracts, instead of N large files.

```jsonc
{
  "v": 1,
  "db_id": "thinkelastic/openfpgaos",
  "timestamp": 1730000000,
  "base_files_url": "https://raw.githubusercontent.com/thinkelastic/openfpgaOS/dist/dist/mister/release/",
  "files": {
    "_Console/openfpgaOS.rbf":        { "hash": "<md5>", "size": 3988580, "reboot": true },
    "games/openfpgaOS/boot.rom":      { "hash": "<md5>", "size": 130764,  "reboot": true },
    "Scripts/openfpgaOS_setup.sh":    { "hash": "<md5>", "size": 4096 }
  },
  "folders": {
    "games/openfpgaOS/": {},
    "games/openfpgaOS/wads/": {},
    "games/openfpgaOS/vhd/": {}
  },
  "archives": {
    "openfpgaos_shells": {
      "format": "zip",
      "extract": "selective",
      "description": "Installing openfpgaOS Doom images",
      "archive_file": {
        "hash": "<md5 of shells.zip>", "size": 0,
        "url": "https://github.com/thinkelastic/openfpgaOS/releases/download/mister-latest/openfpgaos-shells.zip"
      },
      "summary_inline": {
        "files": {
          "games/openfpgaOS/boot.vhd":         { "hash": "<md5>", "size": 0, "arc_id": "openfpgaos_shells", "arc_at": "boot.vhd" },
          "games/openfpgaOS/vhd/freedoom1.vhd": { "hash": "<md5>", "size": 0, "arc_id": "openfpgaos_shells", "arc_at": "vhd/freedoom1.vhd" },
          "games/openfpgaOS/vhd/sigil.vhd":     { "hash": "<md5>", "size": 0, "arc_id": "openfpgaos_shells", "arc_at": "vhd/sigil.vhd" },
          "games/openfpgaOS/vhd/rekkr.vhd":     { "hash": "<md5>", "size": 0, "arc_id": "openfpgaos_shells", "arc_at": "vhd/rekkr.vhd" }
        },
        "folders": { "games/openfpgaOS/vhd/": { "arc_id": "openfpgaos_shells" } }
      }
    }
  }
}
```

User installs by adding to `/media/fat/downloader.ini`:

```ini
[thinkelastic/openfpgaos]
db_url = 'https://raw.githubusercontent.com/thinkelastic/openfpgaOS/dist/dist/mister/db/openfpgaos.json.zip'
```

Then `update_all` (or `Scripts/downloader.sh`) syncs it.

## 4. `external_files.csv` for archive.org freeware

For freeware IWADs/mods too large to keep in a repo/release (Freedoom, REKKR, SIGIL,
gzdoom-free TCs), reference their archive.org mirrors. `setup.sh` (or a companion DB
`archives` entry pointing at the archive.org zip) fetches them. Keep commercial content out
entirely. (Exact CSV columns to be confirmed against the Downloader source — the two docs we
read cover `files`/`archives`; `external_files.csv` is a separate Downloader feature.)

## 5. Build pipeline (GitHub Action)

`.github/workflows/mister-db.yml` (draft here):
1. Build the MiSTer artifacts (`make build` for the core, `make TARGET=mister os.bin`, and
   the Doom `app.elf` from the game repo) — or download them from an upstream build.
2. Assemble the **freeware** vhd shells with the SDK (`mkfamily`/`mkinstance` restricted to
   freely-licensed wads) — these carry `/app.elf`, `/os.ini`, preallocated `/config` +
   `/saves`, and any freeware wad; **no commercial IWADs**.
3. `zip` the shells → `openfpgaos-shells.zip`; attach it + the rbf/os.bin to a GitHub
   **release** (tag `mister-latest`).
4. Generate the DB JSON with the community template generator
   (`theypsilon/Downloader_DB-Template_MiSTer` `build_db.py`) or a small inline script that
   walks `dist/mister/release/`, computes MD5+size, and fills the `files`/`archives` blocks
   above; publish `openfpgaos.json.zip` (raw file in a `dist` branch, or a release asset).

## 6. `setup.sh` (on-device, run once from Scripts)

Because commercial IWADs and PCM music can't ship, `setup.sh` builds `vhd/doom.vhd`
(family, holds IWADs) and `vhd/doommusic.vhd` (PCM music) from the wads the user dropped in
`games/openfpgaOS/wads/`. It uses **loop-mount + cp** (the MiSTer kernel has loop + vfat —
no host tools needed) against pre-formatted shell images the release ships, or `dd` +
`mkfs.vfat` if building from scratch. See `setup.sh` in this folder for the draft. It is
idempotent and validates each wad by size/name before injecting.

## 7. Open decisions for the PR

1. **Repo split**: the core (openfpgaOS) and the Doom engine (Doom repo) are separate — does
   the DB ship one combined "openfpgaOS Doom" product, or a base core DB + per-game DBs?
2. **Shell vs on-device build**: ship pre-formatted freeware shells + inject IWADs (simpler,
   chosen above), or ship only tooling and build every vhd on-device (heavier).
3. **Music**: build `doommusic.vhd` on-device from IWADs (needs the mus-extract step) vs.
   leave PCM music opt-in.
4. Whether to reuse the community `build_db.py` template (recommended — it's what the
   ecosystem expects) or a self-contained generator.
