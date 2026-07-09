# openfpgaOS — MiSTer distribution (Downloader DB)

Draft distribution design for shipping openfpgaOS + the Doom engine to MiSTer users
through the standard **Downloader / `update_all`** mechanism (custom database), instead of
hand-copying files.

Status: **partially implemented.** `make package TARGET=mister` (openfpgaOS root) now builds
the core release — `releases/mister/openfpgaos-core-v<ver>.zip` (`_Computer/OpenfpgaOS.rbf`
+ `games/OpenfpgaOS/boot.rom` + `INSTALL.txt`) — and a Downloader DB (`openfpgaos.json.zip`,
via the SDK's `mkdb.py`); `make release TARGET=mister` drafts the GitHub release
(`dist/mister/release.sh`). The `archives`/`vhd`-shell optimisation (§3) and the on-device
`setup.sh` wad-build (§6) remain drafts.

---

## 1. The core constraint: engine is free, game data is not

| Layer | Distributable? | Delivery |
|---|---|---|
| Core bitstream `openfpgaOS.rbf` | ✅ our code (Apache) | Downloader DB `files` |
| `boot.rom` (os.bin) | ✅ our code | Downloader DB `files` |
| Doom engine `app.elf` | ✅ our code (chocolate-doom GPL) | inside a shipped vhd shell |
| **Commercial IWADs** (doom2/plutonia/tnt/doom/ultimatedoom.wad) | ❌ copyrighted | **user provides** → `setup.sh` injects |
| **PCM music** (DOOMMUS/DOOM2MUS.wad) | ❌ derived from IWAD | **user builds** → `setup.sh` |
| Freeware mods/TCs (Freedoom, REKKR, SIGIL, D4V, …) | ✅ freely licensed | listed in `external_files.csv`, folded into the DB by `mkdb.py` (own archive.org/GitHub URLs) |

So the shippable payload is the **engine + freeware instance shells**; the user's own
IWADs are merged in on-device by `setup.sh` (run once from the MiSTer *Scripts* menu).

## 2. On-SD layout the DB installs

Paths are relative to `/media/fat` (Downloader path rules: relative, no `..`, not
`/linux`, `/saves`, `MiSTer`, `menu.rbf`, `MiSTer.ini`).

```
_Computer/openfpgaOS.rbf                        core bitstream        (DB file)
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
    "_Computer/openfpgaOS.rbf":       { "hash": "<md5>", "size": 3988580, "reboot": true },
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

## 4. `external_files.csv` — a BUILD input, not a device file

**Correction (important):** `external_files.csv` is **not** something the MiSTer reads at
runtime and it is **not** a Downloader feature. It is a *build-time input* to our database
**generator** (`mkdb.py`, see §5): the generator reads it and folds each freeware entry into
the generated `db.json.zip`, exactly as a `files` entry with the wad's own absolute URL. The
CSV itself never ships to the card — only the wads it names do, delivered by the ordinary
Downloader `files` mechanism. (This mirrors theypsilon's `DB-Template_MiSTer` `build_db.py`,
where "the file won't show up on your device, but the files listed inside it will".)

The delivery pipeline end to end:

```
repo shippable files (staging tree)  ─┐
external_files.csv (freeware wads) ───┼─► mkdb.py ─► <game>.json.zip ─► publish
db_id / base URL / timestamp ─────────┘                                    │
                                                                           ▼
        user adds  [<db_id>] db_url=<url>  to /media/fat/downloader.ini ──► Downloader syncs
```

`db.json.zip` is the **only** Downloader-consumed artifact — and despite the `.zip` name it is
a *real ZIP archive* holding one `<game>.json` (the Downloader loads it with `zipfile`; it is
**not** gzip). Our CSV columns are the generator's own contract, kept human-editable:

```
sd_path , url , size , md5 , tags
```

* `sd_path` — on-SD path relative to `/media/fat` (mirrors the DB layout).
* `url` — absolute `http(s)` source for the exact hosted file.
* `size` / `md5` — bytes (int) and lowercase 32-hex MD5 of that file.
* `tags` — space-separated Downloader filter tags (e.g. `doom freeware`), so a user can
  exclude them with `filter = !freeware` in `downloader.ini`.

Rows with any `TODO_`/missing `url`/`size`/`md5` are **skipped with a warning** (the DB stays
valid; those wads simply aren't offered until a maintainer pins a real size + MD5 — hashes are
never invented). Keep commercial content out of the CSV entirely; the user's own IWADs are
merged on-device by `setup.sh`.

## 5. Build pipeline (GitHub Action)

`.github/workflows/mister-db.yml` (draft here):
1. Build the MiSTer artifacts (`make build` for the core, `make TARGET=mister os.bin`, and
   the Doom `app.elf` from the game repo) — or download them from an upstream build.
2. Assemble the per-game bundle with the SDK (`make package TARGET=mister` → `mkgame.sh`):
   a read-only `boot.vhd` shell + writable saves image + `.mgl` launchers, carrying only
   freeware wads with preallocated `/config` + `/saves`; **no commercial IWADs** (the user
   supplies those on-device via `setup.sh`).
3. `zip` the shells → `openfpgaos-shells.zip`; attach it + the rbf/os.bin to a GitHub
   **release** (tag `mister-latest`).
4. Generate the DB with our own self-contained generator
   `src/sdk/platforms/mister/mkdb.py` — it walks the staging tree, computes each file's real
   MD5 + size, folds `external_files.csv` in (§4), and writes a valid `openfpgaos.json.zip`
   (ZIP-wrapped JSON) plus the `downloader.ini` snippet. Publish the `.json.zip` (raw file in
   a `dist` branch, or a release asset) and point `db_url` at it. For the Doom per-game
   bundle this is wired into `make package TARGET=mister` (emits `releases/mister/doom.json.zip`).
   (`mkdb.py` currently emits plain `files`/`folders` delivery — each local file hosted at
   `base_files_url + <path>`; the `archives` shell-zip optimization in §3 is a future add.)

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
4. ~~Whether to reuse the community `build_db.py` template or a self-contained generator.~~
   **Decided:** self-contained `mkdb.py` (emits the same Downloader `db.json.zip` format,
   no GitHub-Actions/template dependency; `external_files.csv` is its build input).
