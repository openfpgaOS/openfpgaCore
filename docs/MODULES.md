# openfpgaOS — Module System (design)

**Goal.** Turn every optional feature into a **module** with uniform `INCLUDE_<MODULE>` semantics, so a
core is *composed* from the modules a given game needs — up to a fully custom per-game core. Three rules:

1. **Additive `INCLUDE_<MODULE>`, default OFF.** A build lists what it *includes*. No `EXCLUDE_*`.
2. **Zero cost when excluded.** A non-included module synthesizes to nothing — no ALM, no M10K, no caps
   bit, no decode term, and for CPU modules, not in the netlist.
3. **Dependencies auto-resolve.** Including a module pulls in what it needs (`INCLUDE_COMBINE` ⇒ truecolor),
   via derived `localparam EFF_* = OR of dependents`. One source of truth; no per-layer drift.

**Granularity = coarse** (decided): one module per *coherent capability*, not per internal sub-knob. The
whole transform front-end is one `XFORM` module, not five. Features that distinguish *different games*
(triangle opcodes, span vs column, color models) stay separate so a per-game core stays lean.

> Precedent in-tree: `INCLUDE_TRI_WALKER` (gpu_core.v:2624) is a derived dependency param that
> conditionally instantiates the **separate-file** `gpu_edge_walker.v` under `generate if (...) begin :
> g_tri_walker` (2629) → zero cost when excluded. `INCLUDE_COLUMN_LIST_EFF` (1375) is a 2nd example.

---

## Conventions

### Polarity & defaults
- Every module: `parameter INCLUDE_<MODULE> = 0` (off). Retire all `EXCLUDE_*` (COMBINE, CLIP_TRI,
  GPU_XFORM_MAC, PALETTE, PARALLEL_DIVS, TRANSLUC).
- Non-feature numeric tuning stays a plain param, but **derived** from module choices where sensible
  (`GPU_Z_READ_WINDOW` = 4 if any triangle module else 1; `GPU_EW_PARALLEL_DIVS` from the build).

### Two zero-cost mechanisms
- **RTL gen-guard** (GPU + peripherals): `[file]` modules instantiated under `generate if (INCLUDE_X)`
  (true zero cells, e.g. `gpu_edge_walker.v`, `audio_mixer.v`, CRAM1 stack, SNAC); `[gen]` interwoven
  cones wrapped in `generate if` that const-fold. Migrate `[gen]→[file]` opportunistically.
- **Netlist-select** (CPU/memory): FPU/cache/extensions aren't gen-guards — they change the VexiiRiscv
  netlist. A CPU module resolves to `generate_vexii.sh` args + the `vexii_active.v` select + the
  `cpu_system`/PMA config. "Excluded" = the feature is genuinely absent from the generated netlist.

### Dependency resolver (single source of truth)
```verilog
// requested (from the build) → resolved (what gets built); everything keys off EFF_*:
localparam EFF_TRUECOLOR   = INCLUDE_TRUECOLOR | INCLUDE_COMBINE | INCLUDE_XFORM;
localparam EFF_XFORM       = INCLUDE_XFORM;                              // bundle: mac+vtxcache+light+clip
localparam EFF_TRI_WALKER  = INCLUDE_PARAM_TRI | INCLUDE_VERT_TRI | INCLUDE_PARAM_TRI_RECS | INCLUDE_XFORM;
localparam EFF_COMPACT_SPAN= INCLUDE_COMPACT_SPAN | INCLUDE_COLUMN_LIST;
```
So `INCLUDE_COMBINE=1` alone forces truecolor; illegal combos are impossible by construction.

### Caps
Every module gets **one** caps bit, advertised as `EFF_<MODULE> ? bit : 0`. `of_caps.h` defines exactly the
advertisable bits. core_top wires **every** param on **every** target (no relying on defaults). Apps gate
emitters on `of_has_feature()` only. Canonical name **`TRUECOLOR`** replaces the VCOLOR/DIRECT_COLOR triad.

---

## Module list

**BASE (always present — not a module).** Shared rasterizer: span producer + 1px/cyc fragment pipe +
perspective + edge core + z-test/write; SoC: CPU core, SDRAM, scanout, save-slots. Caps:
`GPU_SPAN`(4), `PERSP`(13), `FRAGPIPE`(14), `PARAM_SPAN_LIST/Z/ZTEST/Q29`(15–18), `SAVE_SLOTS`(9),
`SAVE_DT_WORD`(24). "Mixer available" `OF_HW_MIXER`(0) stays set (SW mixer when `HW_MIXER` off).

### GPU — geometry front-ends (separate: distinguish games)
| Module | `INCLUDE_` | Opcodes | Depends on | Caps | Code |
|---|---|---|---|---|---|
| Param-triangle | `PARAM_TRI` | 0x49 | TRI_WALKER | 19 | [gen]→[file] |
| Vertex-triangle | `VERT_TRI` | 0x4A/0x4B | TRI_WALKER | 20 | [gen] |
| Param-tri records | `PARAM_TRI_RECS` | 0x4D | VERT_TRI (0x4A state) | 22 | [gen] |
| Compact span | `COMPACT_SPAN` | 0x48 compact | — | 23 | [gen] |
| Column list | `COLUMN_LIST` | 0x4C | COMPACT_SPAN | 21 | [gen] |

### GPU — color / shading (separate: distinct color models)
| Module | `INCLUDE_` | What | Depends on | Caps | Code |
|---|---|---|---|---|---|
| Truecolor RGB565 | `TRUECOLOR` | direct-color fragment path | — | 10 (rename) | [gen] |
| Combiner texel·C+D | `COMBINE` | HILITE/specular (Mario head) | TRUECOLOR | 27 | [gen] |
| Palette / colormap | `PALETTE` | 8-bit colormap lane + port-B | — | *new (5)* | [gen] |
| Translucency | `TRANSLUC` | transluc LUT + FBSS_BLEND | — | 12 (ALPHA) | [gen] |

### GPU — transform front-end (BUNDLED, coarse)
| Module | `INCLUDE_` | Bundles | Depends on | Caps | Code |
|---|---|---|---|---|---|
| Transform | `XFORM` | 0x52 xform + 0x50/51 matrix-MAC + 0x53/54 vtx-cache + 0x55/57 lighting + 0x4F clip | TRUECOLOR, TRI_WALKER | 26 | [gen]→[file] |

> Was 5 params (XFORM_RGB/GPU_XFORM_MAC/VTX_CACHE/GPU_LIGHT/CLIP_TRI). Coarse-bundled into one `XFORM`
> (you'd never want lighting without transform). Currently unused by shipping games (SM64/Quake2 do CPU
> geometry) — stays an available, off-by-default module.

### GPU — memory & numeric
| Module / param | `INCLUDE_` / param | What | Caps | Code |
|---|---|---|---|---|
| Fast texture (CRAM1) | `TEX_MEM` | dedicated sync-burst tex chip | 25 | [file] |
| Z-read window | `GPU_Z_READ_WINDOW` (derived) | 4 if any tri module else 1 | — | param |
| Edge parallel divs | `GPU_EW_PARALLEL_DIVS` (derived) | concurrent slope dividers | — | param |

### CPU / memory (netlist-select modules)
| Module | `INCLUDE_` | What | Resolves to | Caps |
|---|---|---|---|---|
| Hardware FPU | `FPU` | RISC-V F ext (else soft-float) | generate_vexii.sh FPU on/off | 8 |
| Cache profile | `CPU_CACHE` (size param) | I$/D$ sizes | generate_vexii.sh ICACHE_SETS/DCACHE_SETS | — |
| Cache-mgmt ext | `CACHE_MGMT` | Zicbom + Zicboz | generate_vexii.sh flags + cpu_system | *new* |
| L2 cache | `L2` | shared L2 CacheFiber | cpu_system topology | *new* |

> CPU modules are coarse netlist knobs, not RTL gen-guards. ⚠️ FPU is practically always-on today (musl
> /game float); dropping it = soft-float everywhere. Kept a module so a future integer-only core *can*
> drop it (big ALM/M10K). `CPU_CACHE` is a size profile, not on/off. See [[project_vexriscv_config]].

### SoC peripherals
| Module | `INCLUDE_` | What | Caps | Code |
|---|---|---|---|---|
| HW audio mixer | `HW_MIXER` | `audio_mixer.v` (else CPU SW mixer) | 1 | [file] |
| Analogizer / SNAC | `ANALOGIZER` | analog video + SNAC raster (pocket) | 3 | [file] |
| Link cable | `LINK` | serial/link — **stubbed everywhere** (no live HW; advertised on MiSTer only) | 2 | stub |
| 4-player input | `4PLAYER` | **pocket** controller expansion (`ifndef` gate, core_top.v:2603); dead macro on MiSTer | *new* | [gen] |

---

## Dependency graph

```
TRUECOLOR ──┬── COMBINE
            └── XFORM (mac · vtx-cache · lighting · clip bundled)
TRI_WALKER  ◄── PARAM_TRI, VERT_TRI, PARAM_TRI_RECS, XFORM       (shared edge walker, separate file)
COMPACT_SPAN ◄── COLUMN_LIST
PARAM_TRI_RECS ── needs VERT_TRI's 0x4A sticky state
CPU: FPU / CPU_CACHE / CACHE_MGMT / L2 independent (netlist-select)
```
Pull-up resolution: requesting a leaf forces its infrastructure (`EFF_*` OR-reduction). Include nothing ⇒
BASE rasterizer + minimal CPU only.

---

## Per-game profiles (validates the module set; the point of custom cores)

| Core | Geometry | Color | Other GPU | CPU | Periph |
|---|---|---|---|---|---|
| **2D** (Diablo/ScummVM point-and-click) | — (BASE rasterizer only) | PALETTE + TRANSLUC | — | FPU, **dual-issue**, 64 KB D$ | HW_MIXER, ANALOGIZER, 4PLAYER |
| **2.5D** (Doom/Duke/Wolf/ScummVM) | COMPACT_SPAN + COLUMN_LIST | PALETTE + TRANSLUC | — | FPU | HW_MIXER, ANALOGIZER |
| **Quake1 (SW)** | PARAM_TRI | PALETTE | — | FPU | HW_MIXER, ANALOGIZER |
| **SM64** | VERT_TRI | TRUECOLOR + COMBINE | Z-window=4 | FPU | HW_MIXER |
| **Quake2** | VERT_TRI + PARAM_TRI_RECS | TRUECOLOR | Z-window=4 | FPU | HW_MIXER |
| **MiSTer (all)** | all | all | TEX_MEM | FPU + L2 | HW_MIXER |

(Today's variants map to these: os20 = the 2D row (dual-issue CPU — the span-form cuts fund the second
issue lane); os25 = the 2.5D + Quake1 union; os30 = SM64 ∪ Quake2 — **all use the HW
mixer**: every `variants/<v>.mk` DEFS list carries `INCLUDE_HW_MIXER`.)

> **LINK and 4PLAYER are NOT required by MiSTer** (verified): LINK is *stubbed* in `emu.sv` — advertises
> caps bit 2 but `link_irq=0`/`link_reg_rdata=0` with `.link_reg_*()` ports unconnected (no HW, no firmware
> check of `OF_HW_NET`). 4PLAYER is a *dead macro* on MiSTer — the only gate is `ifndef INCLUDE_4PLAYER` in
> pocket `core_top.v:2603`; no MiSTer RTL reads it (MiSTer multiplayer comes via HPS/USB). Both should drop
> from MiSTer's `INCLUDE_DEFS`. LINK has no live implementation on *any* target → keep only as a
> placeholder module (or remove); 4PLAYER is really a **pocket input** module, currently never enabled.

---

## Cleanup folded into the refactor (from the 2026-06-27 gate audit)
- **Wire MiSTer explicitly** — `emu.sv` under-wires ~14 gpu_core/periph params (take defaults today).
- **LINK is dead** — stubbed in `emu.sv` (advertised bit 2, no HW), absent on pocket. Drop from MiSTer
  `INCLUDE_DEFS`; keep `LINK` only as a placeholder module (or remove the cap) until real link HW exists.
- **4PLAYER mis-placed** — dead macro in MiSTer `INCLUDE_DEFS`; the real gate is pocket-only
  (`core_top.v:2603`). It's a pocket-input module; drop it from MiSTer.
- **Orphaned caps** — `BILINEAR`(11) decide/drop; `ALPHA`(12) → wire to `TRANSLUC`.
- **Name triad** — `VCOLOR`/`DIRECT_COLOR`/truecolor → `TRUECOLOR` on bit 10.
- **Missing caps** — `PALETTE`/`XFORM`(clip/mac) get bits; CACHE_MGMT/L2/4PLAYER get bits.
- **Fast-tex story** — `of_caps.h`/periph comments claim os30 sets `FAST_TEX`, but no variant defines
  `INCLUDE_TEX_MEM` (CRAM1 reverted); fix the comments.
- **Uniform check** — SM64 reads `OF_HW_GPU_SPAN` by raw bit-test; use `of_has_feature()`.
- **Reserved bits** — mark bit 5 (now PALETTE), document free bits 28+.

---

## Suggested phasing
0. **Spec** — module list + dep graph + granularity + CPU-scope (← agreed here).
1. **Resolver + uniform polarity** — `EFF_*` localparams in gpu_core, `EXCLUDE_*`→`INCLUDE_*` (default 0),
   bundle the 5 xform params into `XFORM`, caps key off `EFF_*`. No behavior change. Verilator-gate.
2. **Explicit wiring + single source** — every param wired on every target (Pocket + MiSTer); Makefile,
   core_top, caps all derive from one module list. Re-fit each variant.
3. **Variants → module-lists** — express os25/os30/mister as additive `INCLUDE_` sets; add the per-game
   profiles above as buildable cores.
4. **CPU modules** — fold FPU/cache/ext/L2 into the resolver (netlist-select via generate_vexii.sh).
5. **Extract [gen]→[file]** — lift cleanly-separable cones (XFORM front-end, palette lane) into own modules.

Each phase is independently shippable + Verilator-gated; runtime behavior is unchanged until a build's
module list changes.
