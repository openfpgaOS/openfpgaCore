# Multi-instance launcher (menu.elf) — MiSTer

How several game "instances" live on one MiSTer SD image and how the in-OS
launcher (`menu.elf`) switches between them **without an FPGA reset**
(Architecture A). This is the MiSTer counterpart to the Analogue Pocket's
host-driven instance browser — on the Pocket the Analogue OS picks the
instance and reloads the core; MiSTer has no such host, so openfpgaOS does it
in-OS.

> MiSTer only. The single full-feature MiSTer bitstream runs every instance,
> and saves are write-through FAT files (durable per write). On the Pocket the
> host supplies per-instance nonvolatile files and `of_relaunch()` returns
> `OF_ERR_NOT_SUPPORTED`; none of the layout below applies there.

---

## 1. Disk layout

The MiSTer disk image is a FAT volume. The image **root** holds the launcher;
each game is a self-contained subdirectory of `/games/`:

```
/os.ini                 [os] ELF=app.elf         (boots the launcher)
/app.elf                menu.elf                  (the launcher)
/config/shared.cfg      slot 8  — shared across ALL instances
/games/
  Quake/
    os.ini              [os] ELF=app.elf, ARGS=…
    app.elf             the game
    *.pak *.wad …       data files (slots 4-6, 20+; enumerated)
    bank.ofsf           optional SoundFont (slot 7)
    config/duke3d.cfg   slot 9  — per-instance settings
    saves/
      slot_0.sav … slot_9.sav   slots 10-19, pre-created full capacity
  Doom/
    os.ini
    app.elf
    …
```

A directory under `/games/` is a **launchable instance only if it contains an
`os.ini`** — `of_file_list_instances()` "reads the ini" to qualify each
candidate. The launcher lists qualifying directories by name.

### Save preallocation invariant

Every `slot_N.sav` (and per-instance `config/duke3d.cfg`) **must be
pre-created at full capacity** (256 KB) by the image builder. Runtime writes
are `f_lseek`+`f_write`+`f_sync` into existing clusters only; a write past EOF
is refused (it would grow the file and rewrite FAT metadata, turning a power
cut into filesystem corruption). The image builder owns preallocation.

---

## 2. Boot / switch flow

1. Cold boot: the HPS delivers `boot.rom` (os.bin); the OS reads root `/os.ini`
   → launches `/app.elf` = `menu.elf`.
2. `menu.elf` calls `of_instance_list()` (lists `/games/*` with an os.ini),
   draws a picker, and registers itself as the exit target via
   `of_launch_set_menu("", "app.elf")`.
3. On selection it calls `of_relaunch("/games/<name>", NULL)`. The kernel tears
   down the launcher, points the file HAL at the instance, re-reads
   `/games/<name>/os.ini`, and execs that instance's `app.elf` — no FPGA reset.
4. When the game `exit()`s, the OS relaunches the registered menu.

Instance scoping is entirely in the MiSTer file HAL: with no instance set every
path is byte-identical to the legacy single-game layout, so single-game images
(no `/games/`) keep working unchanged.

### Slot → path resolution (MiSTer)

`slot_path()` (`targets/mister/file.c`) resolves a game's own files under the
active `instance_root`; `shared.cfg` stays global:

| slot | file (instance set)                | scope        |
|------|------------------------------------|--------------|
| 2    | `/games/<n>/os.ini`                | per-instance |
| 3    | `/games/<n>/app.elf`               | per-instance |
| 7    | `/games/<n>/bank.ofsf`             | per-instance |
| 8    | `/config/shared.cfg`               | **global**   |
| 9    | `/games/<n>/config/duke3d.cfg`     | per-instance |
| 10-19| `/games/<n>/saves/slot_N.sav`      | per-instance |
| 4-6,20+| enumerated under `/games/<n>/` and `/games/<n>/assets/` | per-instance |

---

## 3. API (apps)

`#include "of.h"` (`api/of_launch.h`):

```c
int of_instance_list(char *names, unsigned stride, unsigned max);
int of_relaunch(const char *instance, const char *elf);   /* no return on success */
int of_launch_set_menu(const char *instance, const char *elf);
```

`of_relaunch(instance, elf)`: `instance` = `/games/<name>` or NULL/"" for the
root; `elf` = a filename / `slot:<N>` / NULL to take `[os] ELF` from the
instance's os.ini.

Source: `src/apps/menu/`. Build against a generated SDK
(`make sdk DEST=<dir>`): `make -C src/apps/menu SDK_DIR=<dir>/src/sdk`. Deploy
the resulting `app.elf` as the image-root `/app.elf`.

---

## 4. Image builder

Assembling the `/games/` tree and preallocating the save/config files is the
job of the SDK image builder (`openfpgaSDK platforms/mister/mkimage.sh`), which
lives in the SDK/game repo, not here. It must:

- place the launcher at root (`/os.ini`, `/app.elf` = menu.elf, `/config/shared.cfg`);
- create one `/games/<name>/` per instance with `os.ini`, `app.elf`, data files,
  optional `bank.ofsf`, `config/duke3d.cfg`;
- pre-create `/games/<name>/saves/slot_0..9.sav` at full capacity.
