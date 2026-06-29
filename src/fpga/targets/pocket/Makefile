#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------

# openfpgaOS — Analogue Pocket Build System
#
# Quick start:
#   make          Full build: cpu → firmware → compile → test → deploy
#   make flash    Quick: firmware → patch bitstream → deploy (~10 sec)
#   make help     Show all targets

# ── Configuration ────────────────────────────────────────────────────
PROJECT    = ap_core
TARGET     = pocket
FW_DIR     = ../../../firmware/os
VEXII_DIR  = ../../vendor/vexriscv/VexiiRiscv
# Per-variant CPU netlist (generated separately): VexiiRiscv_os25.v / _os30.v.
VEXII_OUT  = $(VEXII_DIR)/VexiiRiscv_$(VARIANT).v
TEST_DIR   = ../../test
ROOT_DIR   = ../../../..
RELEASE_DIR = $(ROOT_DIR)/build/pocket/Cores/ThinkElastic.openfpgaOS
ASSETS_DIR  = $(ROOT_DIR)/build/pocket/Assets/openfpgaos/common
TOOLS_DIR   = $(ROOT_DIR)/tools
REVERSE_BITS = $(TOOLS_DIR)/reverse_bits

QUARTUS    = /home/alberto/altera_lite/25.1std/quartus/bin
export PATH := $(QUARTUS):$(PATH)

# ── Variant + per-variant build dir (defined early: used by rules below) ──
# os25 (default) / os30 — see the detailed VARIANT block further down for the
# feature sets.  BDIR/QOUT give each variant its OWN db/ + output_files/ so
# os25 and os30 build with zero shared writable paths (concurrent-safe).
VARIANT ?= os25
# JOB names the build INSTANCE.  Defaults to the variant (so `make build
# VARIANT=os30` → bld/os30), but a parallel seed search assigns distinct jobs
# (e.g. JOB=os30-s7) so MANY builds of the SAME variant get separate bld/<job>/
# dirs and never collide.  SEED can be overridden per job on the command line.
JOB ?= $(VARIANT)
BDIR := bld/$(JOB)
QOUT := $(BDIR)/output_files

# ── Colors ───────────────────────────────────────────────────────────
ifneq ($(shell tput colors 2>/dev/null),)
C_LOGO  := \033[96m
C_HEAD  := \033[1m
C_CMD   := \033[93m
C_OK    := \033[32m
C_WARN  := \033[33m
C_ERR   := \033[31m
C_DIM   := \033[2m
C_RESET := \033[0m
else
C_LOGO  :=
C_HEAD  :=
C_CMD   :=
C_OK    :=
C_WARN  :=
C_ERR   :=
C_DIM   :=
C_RESET :=
endif

# ── Default ──────────────────────────────────────────────────────────
all: help

# ── Full build ───────────────────────────────────────────────────────
# bootloader → os → build → summary. `build` itself auto-runs the cpu
# generation (file-target rule below) when VexiiRiscv.v is missing.
# Verilator tests are NOT part of a full build — run them explicitly
# via `make test` when you've changed RTL.
full: cpu bootloader os build summary

# ── Help ─────────────────────────────────────────────────────────────
help:
	@printf "$(C_LOGO)"
	@echo "  openfpgaOS — $(TARGET)"
	@printf "$(C_RESET)\n"
	@printf "  $(C_HEAD)Build:$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)full$(C_RESET)              bootloader → os → build            $(C_DIM)(~9 min)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)build$(C_RESET)             cpu → container compile → rbf      $(C_DIM)(~7 min)$(C_RESET)\n"
	@printf "      $(C_DIM)VARIANT=os25|os30  → os25.rbf_r / os30.rbf_r (seed stored in seeds/<variant>.seed)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)build-all$(C_RESET)         all variants in parallel containers $(C_DIM)(~7 min)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)firmware$(C_RESET)          bootloader → os → MIF patch        $(C_DIM)(~10 sec)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)os$(C_RESET)                Builds os.bin                      $(C_DIM)(~5 sec)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)clean$(C_RESET)             Remove all build artifacts\n"
	@echo ""
	@printf "  $(C_HEAD)Development:$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)test$(C_RESET)              Run Verilator test suite           $(C_DIM)(~30 sec)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)check$(C_RESET)             RTL check (Analysis & Synthesis)   $(C_DIM)(~45 sec)$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)timing$(C_RESET)            Show Fmax and slack from last build\n"
	@printf "    $(C_CMD)make $(C_CMD)sweep$(C_RESET)             Parallel container seed search     $(C_DIM)(MAXJOBS-wide)$(C_RESET)\n"
	@printf "      $(C_DIM)VARIANT=os30 SWEEP_MIN=1 SWEEP_MAX=12 MAXJOBS=4  → seeds/<variant>.seed$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)sdk $(C_CMD)DEST=$(C_CMD)\"path\"$(C_RESET)   Sync headers + runtime to SDK repo(s)\n"
	@printf "    $(C_CMD)make $(C_CMD)program$(C_RESET)           JTAG program via USB Blaster\n"
	@echo ""
	@printf "  $(C_HEAD)Setup (one-time per machine):$(C_RESET)\n"
	@printf "    $(C_CMD)make $(C_CMD)quartus-image$(C_RESET)\n"
	@printf "      $(C_DIM)Bake Quartus into a local image so `make build` works without a host install.$(C_RESET)\n"
	@printf "      $(C_DIM)Place quartus.tar.gz at tools/docker/ first — see tools/build-quartus-image.sh.$(C_RESET)\n"
	@echo ""

# ── CPU generation (file target) ─────────────────────────────────────
# $(VEXII_OUT) = VexiiRiscv/VexiiRiscv_<variant>.v — the SpinalHDL-generated
# top wrapper Quartus reads.  The per-variant netlist is wired into the
# generated bld/<job>/ap_core.qsf as a VERILOG_FILE (see the qsf rule below),
# so no preprocessor-macro indirection or shim is needed.  Generated by
# generate_vexii.sh invoking stock vexiiriscv.Generate. Not tracked in git —
# a fresh checkout triggers the recipe automatically the first time `make build`
# runs.  Runs in a Docker container by DEFAULT (mirrors the Quartus build
# container) so a fresh machine needs only Docker + internet — no host JDK/sbt:
# the openfpgaos-vexii image auto-builds on first use and the Scala/SpinalHDL deps
# cache in tools/.vexii-home.  Set USE_VEXII_CONTAINER=0 to use host sbt instead.
USE_VEXII_CONTAINER ?= 1

# Firmware (bootloader + os.bin) runs in a Docker container by DEFAULT — many
# hosts (notably Homebrew's riscv64-elf-gcc on macOS) ship the compiler
# without a C library, breaking `#include <string.h>` even in -ffreestanding
# mode.  The container bakes in a newlib-equipped riscv64-unknown-elf toolchain
# so the build is identical across Linux and macOS hosts.  Set
# USE_FIRMWARE_CONTAINER=0 to use the host toolchain instead.
USE_FIRMWARE_CONTAINER ?= 1
ifeq ($(USE_FIRMWARE_CONTAINER),1)
FW_MAKE = bash $(ROOT_DIR)/tools/firmware-container.sh
else
FW_MAKE = $(MAKE) -C $(FW_DIR)
endif
$(VEXII_OUT): | $(VEXII_DIR)/build.sbt
	@printf "$(C_HEAD)[cpu]$(C_RESET) Generating VexiiRiscv ($(VARIANT), file missing)...\n"
ifeq ($(USE_VEXII_CONTAINER),1)
	@bash $(ROOT_DIR)/tools/vexii-container.sh $(VARIANT)
else
	@cd $(VEXII_DIR) && bash ../generate_vexii.sh $(VARIANT)
endif
	@printf "$(C_OK)[cpu] Done$(C_RESET)\n"

# Submodule auto-init: on a fresh clone the VexiiRiscv submodule is not
# checked out (only an empty dir / stale target/ may exist). Trigger
# `git submodule update --init` the first time anything needs build.sbt.
# If the pinned commit is orphaned on the remote (e.g. an unpushed rebase),
# fall back to the openfpgaos-patches branch so a fresh clone still builds
# end-to-end without manual intervention.
VEXII_FALLBACK_BRANCH ?= openfpgaos-patches
$(VEXII_DIR)/build.sbt:
	@printf "$(C_HEAD)[submodule]$(C_RESET) Initializing VexiiRiscv submodule...\n"
	@rm -rf $(VEXII_DIR)/target  # stale sbt scratch can block git clone
	@if ! git -C $(ROOT_DIR) submodule update --init --recursive src/fpga/vendor/vexriscv/VexiiRiscv 2>&1; then \
	    printf "$(C_WARN)[submodule]$(C_RESET) pinned commit unavailable on remote; falling back to $(VEXII_FALLBACK_BRANCH) branch.\n"; \
	    url=$$(git -C $(ROOT_DIR) config -f .gitmodules submodule.src/fpga/vendor/vexriscv/VexiiRiscv.url); \
	    rm -rf $(VEXII_DIR) $(ROOT_DIR)/.git/modules/src/fpga/vendor/vexriscv/VexiiRiscv; \
	    git clone --branch $(VEXII_FALLBACK_BRANCH) --recurse-submodules "$$url" $(VEXII_DIR) || exit 1; \
	fi

# Build the CPU-generation container image (one-time; vexii-container.sh also
# auto-builds it on first `make cpu`).  Mirrors `docker build -t openfpgaos-quartus`.
.PHONY: vexii-image
vexii-image:
	@docker build -t openfpgaos-vexii -f $(ROOT_DIR)/tools/docker/Dockerfile.vexii $(ROOT_DIR)/tools/docker/

# Bake Quartus into a local image (openfpgaos-quartus-full).  One-time setup
# per machine; subsequent `make build` calls reuse it.  Expects a tarball at:
#     tools/docker/quartus.tar.gz   (gitignored)
# Containing `altera_lite/25.1std/quartus/...` at the top level.  Image is NOT
# redistributable (Intel/Altera Quartus EULA) — stays on YOUR machine.  See
# tools/build-quartus-image.sh for the download URL and full instructions.
.PHONY: quartus-image
quartus-image:
	@bash $(ROOT_DIR)/tools/build-quartus-image.sh

# Phony alias to force regeneration: rm $(VEXII_OUT) then `make cpu`,
# or just `make cpu` after deleting the file. Keeps muscle memory alive.
cpu: $(VEXII_OUT)

# ── Build artifact installers ───────────────────────────────────────
#
# Each substantive build step (os/build/firmware) calls one of these to
# refresh its output in $(RELEASE_DIR) so build/ always matches the
# latest in-tree binary. The SDK / SD-card sync runs separately against
# build/.
install-os: $(FW_DIR)/os.bin
	@mkdir -p $(RELEASE_DIR) $(ASSETS_DIR)
	@cp $(FW_DIR)/os.bin $(RELEASE_DIR)/os.bin
	@cp $(FW_DIR)/os.bin $(ASSETS_DIR)/os.bin
	@printf "  $(C_OK)os.bin$(C_RESET)     → $(RELEASE_DIR)/\n"

install-bitstream: $(REVERSE_BITS) $(QOUT)/$(PROJECT).rbf
	@mkdir -p $(RELEASE_DIR)
	@$(REVERSE_BITS) $(QOUT)/$(PROJECT).rbf $(RELEASE_DIR)/$(BITSTREAM).rbf_r > /dev/null
	@# Record which variant produced the current rbf so `make sdk` (which
	@# only sees output_files/) publishes it under the right name.
	@echo $(VARIANT) > $(QOUT)/.last_variant
	@printf "  $(C_OK)$(BITSTREAM).rbf_r$(C_RESET)  → $(RELEASE_DIR)/\n"

# ── Bootloader / OS / Firmware ──────────────────────────────────────
#
# Two source artifacts:
#   - bootloader (firmware.mif): baked into the FPGA BRAM, runs at
#     reset, loads os.bin from CRAM/SD into SDRAM, jumps to it.
#   - os.bin: loaded by the bootloader; everything you actually run.
#
# Three targets:
#   `bootloader` — rebuild firmware.mif and copy into the pocket dir
#                  (where Quartus picks it up at the next compile).
#   `os`         — rebuild os.bin and install into build/.
#   `firmware`   — bootloader → os → MIF patch. The MIF patch updates
#                  any existing .sof in place so you don't need to
#                  re-run Quartus just to ship a new bootloader.
bootloader:
	@printf "$(C_HEAD)[boot]$(C_RESET) Building bootloader (firmware.mif)...\n"
	@$(FW_MAKE) install
	@printf "$(C_OK)[boot] Done$(C_RESET)\n"

os:
	@printf "$(C_HEAD)[os]$(C_RESET) Building os.bin...\n"
	@$(FW_MAKE) os.bin
	@$(MAKE) --no-print-directory install-os
	@printf "$(C_OK)[os] Done$(C_RESET)\n"

firmware: bootloader os
	@if [ -f $(QOUT)/$(PROJECT).sof ]; then \
		printf "$(C_HEAD)[fw]$(C_RESET) Patching .sof MIF with new bootloader ($(VARIANT))...\n"; \
		( cd $(BDIR) && quartus_cdb $(PROJECT) --update_mif > /dev/null 2>&1 && quartus_asm $(PROJECT) > /dev/null 2>&1 ); \
		$(MAKE) --no-print-directory install-bitstream; \
	fi
	@printf "$(C_OK)[fw] Done$(C_RESET)\n"

# ── FPGA compile ─────────────────────────────────────────────────────

# ── Pocket variant selector ──────────────────────────────────────────
# VARIANT picks which Pocket build to produce:
#   os25 (default) — 2.5D games (Doom/Duke3D/ScummVM/Wolf): full feature set,
#                    full HW audio mixer, no hardware vertex-triangle.
#   os30           — Quake2 (3D, pure triangles): hardware vert-tri plane
#                    derivation (0x4A/0x4B) + records-only param-tri (0x4D)
#                    ungated. Pays for it by cutting what Quake2 never uses —
#                    Analogizer/SNAC + the analog raster, GPU blend, link
#                    cable, 4-player input, the 0x48 compact span-group form,
#                    0x4C column lists, the parallel edge-walker dividers —
#                    and replacing the HW mixer with the runtime caps-driven
#                    SW mixer (the bitstream clears OF_HW_MIXER_HW; the single
#                    os.bin auto-selects SW at boot).  Quake1 and the 2.5D
#                    span-group titles stay on os25 (caps bits 21/23 clear
#                    here; the SDK emitters self-gate).  See
#                    docs/os30-quake2-cut-plan.md.
# Firmware is ONE caps-driven os.bin for both — no per-variant build flag.
VARIANT ?= os25

# ── Variant registry ─────────────────────────────────────────────────
# Add a variant in 3 steps: (1) append its name to VARIANTS, (2) add a
# DEFS_<name> line below, (3) drop a seeds/<name>.seed.  Then `make build
# VARIANT=<name>`, `make sweep VARIANT=<name>`, and `make build-all` just work,
# shipping <name>.rbf_r.  A feature is PRESENT iff its INCLUDE_* macro is in the
# variant's list (positive include-set); 4-player, link, vert/param/recs-tri and
# fast tex are simply absent where unlisted.
VARIANTS := os25 os30

# Module lists are ADDITIVE (docs/MODULES.md): a variant lists the INCLUDE_
# modules it wants; everything else is off (zero cost).  core_top wires every
# gpu_core/periph param off these macros, so the gpu_core param defaults never
# apply here.  Dependency pull-ups (combine/xform ⇒ truecolor) live in core_top.
#
# OS25 (default, 2.5D games — Doom/Duke3D/ScummVM/Wolf + Quake1 SW alias):
# analogizer, full HW mixer, translucency, the palettized/colormap lane, column
# lists, compact span groups.  NO triangle path (edge walker folds away), NO
# truecolor/combine/xform, NO fast tex.  (Dropping INCLUDE_XFORM also sheds the
# previously-dead 0x50/0x51/0x4F cones os25 used to synthesize via defaults.)
# INCLUDE_4PLAYER wires APF dock slots 2/3 (cont3/cont4) through to the core so
# the firmware's keyboard/mouse decode (poll_hid_slots) sees real data instead
# of the tied-0 default; without it those slots are hardcoded 0 (core_top.v).
DEFS_os25 := INCLUDE_ANALOGIZER INCLUDE_HW_MIXER INCLUDE_TRANSLUC \
             INCLUDE_PALETTE INCLUDE_COLUMN_LIST INCLUDE_COMPACT_SPAN \
             INCLUDE_PARAM_SPAN_Q29 INCLUDE_4PLAYER

# OS30 (SM64 ∪ Quake2): HW vertex-triangle plane derive (0x4A/0x4B) + truecolor
# RGB565 + the texel*C+D combiner (HILITE/specular, e.g. the Mario head) + HW
# mixer.  INCLUDE_Z_BURST forces GPU_Z_READ_WINDOW=4 (SM64 z-tests every
# fragment).  VEXII_CPU_OS30 selects the os30 CPU netlist.
#   NOT listed (= off, additive): PALETTE (truecolor-only), the XFORM transform
#   front-end (CPU does geometry — sheds 0x52/0x50/0x51/0x53-57/0x4F + xf_M M10K),
#   PARAM_TRI/PARAM_TRI_RECS, COMPACT_SPAN/COLUMN_LIST, TEX_MEM, ANALOGIZER,
#   TRANSLUC, PARALLEL_DIVS.  This is bit-identical to the prior EXCLUDE_* form.
# NOTE: combiner is KEPT (INCLUDE_COMBINE) — gating it freed ~884 ALM but gave NO
# WNS gain (wall is the CPU decode→FPU cone, not GPU congestion) AND darkened the
# Mario head (its brightness is the combiner's additive D term).  The
# OF_HW_GPU_COMBINE caps bit + app g_combine gate stay, so it can be gated
# cleanly in a FUTURE ALM-starved build.  See docs/MODULES.md.
DEFS_os30 := INCLUDE_VERT_TRI INCLUDE_DIRECT_COLOR INCLUDE_COMBINE INCLUDE_Z_BURST \
             VEXII_CPU_OS30 INCLUDE_HW_MIXER

# Fail loudly on an unknown VARIANT (the old ifeq/else silently built os25).
$(if $(filter $(VARIANT),$(VARIANTS)),,$(error Unknown VARIANT '$(VARIANT)' — known variants: $(VARIANTS)))

VARIANT_DEFS := $(DEFS_$(VARIANT))

# Each VARIANT_DEFS entry is forwarded to quartus_map as --verilog_macro=<NAME>.
VERILOG_MACROS = $(foreach d,$(VARIANT_DEFS),--verilog_macro="$(d)")

# Variant-named bitstream.  Deployed cores ship os25.rbf_r / os30.rbf_r
# (the name IS the variant token, and the core.json "filename" matches),
# so an os25 and an os30 core coexist on one SD card / SDK runtime.  Kept
# short + lowercase: the Pocket caps the bitstream filename (~15 chars) —
# the old "bitstream.rbf_r" was exactly 15; longer names fail to load.
BITSTREAM := $(VARIANT)

# Fitter seed — STORED in the repo, per variant, as committed data in
# seeds/<variant>.seed (NOT a build parameter).  os25 and os30 are
# different netlists and close at different seeds, so each gets its own
# file.  `make build` reads it; `make sweep` searches and writes the
# winner back into it.  Set one by hand by editing the file.
SEED_FILE := seeds/$(VARIANT).seed
SEED      := $(strip $(shell cat $(SEED_FILE) 2>/dev/null))

# Generate the per-variant project: rewrite every source path in the master
# ap_core.qsf to absolute (so the deeper bld/ dir resolves them), append the
# variant CPU netlist + macros + seed, drop the master's fixed SEED.
# Regenerate when the source qsf, the stored seed, or the build config (Makefile:
# VARIANT_DEFS / QPROCS) change — so `make build` after a sweep actually picks up
# the new seed and the pinned processor count instead of reusing a stale qsf.
$(BDIR)/ap_core.qsf: ap_core.qsf ap_core.qpf $(SEED_FILE) Makefile | $(VEXII_OUT)
	@mkdir -p $(QOUT)
	@awk -v base="$(CURDIR)" '\
	  $$1=="set_global_assignment" && $$2=="-name" && \
	  ($$3=="VERILOG_FILE"||$$3=="SYSTEMVERILOG_FILE"||$$3=="VHDL_FILE"||$$3=="QIP_FILE"||$$3=="SDC_FILE"||$$3=="MIF_FILE"||$$3=="SOURCE_FILE"||$$3=="SEARCH_PATH"||$$3=="QSYS_FILE") { \
	    if ($$4 !~ /^\//) $$4 = base "/" $$4; print; next } \
	  $$1=="set_global_assignment" && $$2=="-name" && ($$3=="SEED"||$$3=="NUM_PARALLEL_PROCESSORS") { next } \
	  { print }' ap_core.qsf > $(BDIR)/ap_core.qsf
	@# Variant CPU netlist (VexiiRiscv_<variant>.v) — explicit per-job so
	@# `grep VERILOG_FILE bld/<job>/ap_core.qsf` shows exactly which CPU
	@# is being fit, with no preprocessor-macro indirection.  Mirrors how
	@# mister's qsf names its netlist directly.
	@echo "set_global_assignment -name VERILOG_FILE $(abspath $(VEXII_OUT))" >> $(BDIR)/ap_core.qsf
	@for m in $(VARIANT_DEFS); do echo "set_global_assignment -name VERILOG_MACRO $$m" >> $(BDIR)/ap_core.qsf; done
	@echo "set_global_assignment -name SEED $(SEED)" >> $(BDIR)/ap_core.qsf
	@# Pin the fitter's processor count so a single build and every parallel sweep
	@# fit place IDENTICALLY and reproducibly (Quartus's parallel placement varies
	@# with processor count).  QPROCS x MAXJOBS should fit the host cores.
	@echo "set_global_assignment -name NUM_PARALLEL_PROCESSORS $(QPROCS)" >> $(BDIR)/ap_core.qsf
	@# The compile runs with CWD = $(BDIR), but some RTL init_file refs are BARE
	@# relative names resolved from the project dir — notably the boot-ROM
	@# "firmware.mif" (axi_periph_slave) and "./apf/build_id.mif".  Without the
	@# project dir on SEARCH_PATH, Quartus can't find them and inits the BRAM to
	@# all-zeros (Critical Warning 127003) → the boot ROM is dead and the core
	@# never starts.  This is the container-build "stopped booting" bug.
	@echo "set_global_assignment -name SEARCH_PATH $(CURDIR)" >> $(BDIR)/ap_core.qsf
	@cp ap_core.qpf $(BDIR)/ap_core.qpf
	@printf "  $(C_OK)qsf$(C_RESET) → $(BDIR)/ (abspath sources + $(VARIANT) macros + seed $(SEED))\n"

# Variant-named (non-reversed) copy: `make rbf-named VARIANT=os30`
# produces output_files/openfpgaOS30.rbf from the just-built core.
rbf-named: $(QOUT)/$(PROJECT).rbf
	@cp $(QOUT)/$(PROJECT).rbf output_files/$(BITSTREAM).rbf
	@printf "  $(C_OK)$(BITSTREAM).rbf$(C_RESET) → output_files/\n"

# `compile` is kept as an alias for `build` so old muscle memory and
# anything that calls `make compile` still works. Both run a clean
# Quartus full compile (rm -rf db incremental_db + quartus_sh --flow).
compile: build

# build: clean Quartus compile of ONE job, in an isolated Docker CONTAINER.
# Containers are the default build path: each gets a private $HOME
# (~/.altera.quartus) and /tmp — the shared state that makes concurrent host
# Quartus segfault — so any number of builds run at once (that is what lets
# build-all and sweep fan out).  Quartus itself is bind-mounted read-only, not
# baked into the image; build the image once:
#   docker build -t openfpgaos-quartus tools/docker/   (tools/docker/Dockerfile)
# Outputs land in $(BDIR) (its own db/ + output_files/), fully isolated per job,
# with the VERILOG_MACROs + SEED baked into $(BDIR)/ap_core.qsf.
build: $(VEXII_OUT) bootloader $(BDIR)/ap_core.qsf
	@test -n "$(SEED)" || { printf "$(C_ERR)[fpga] No seed stored for VARIANT=$(VARIANT) — expected $(SEED_FILE)$(C_RESET)\n"; exit 1; }
	@printf "$(C_HEAD)[fpga]$(C_RESET) Container compile (VARIANT=$(VARIANT), JOB=$(JOB), seed $(SEED), dir $(BDIR))...\n"
	@bash $(ROOT_DIR)/tools/quartus-container.sh "$(CURDIR)/$(BDIR)"
	@$(MAKE) --no-print-directory install-bitstream
	@printf "$(C_OK)[fpga] Done$(C_RESET)\n"

# Container runtime, mirroring tools/oci.sh's detection (prefer docker, else
# Apple `container`).  This decides the threading model on Apple Silicon below.
OCI ?= $(shell command -v docker >/dev/null 2>&1 && echo docker || { command -v container >/dev/null 2>&1 && echo container; })

# Apple Silicon hosts run Quartus inside a linux/amd64 container under Rosetta.
# Under DOCKER/OrbStack, Rosetta's thread allocation/locking overhead makes
# Quartus die with `Error (112002): Can not read any output from quartus_map`
# when too many threads are spawned (within a fit AND across parallel fits) —
# so we force single-threaded there.  Apple's `container` does NOT have this
# problem: multi-threaded x86 scales fine under its Rosetta (measured ~2x on the
# fitter, 5.7x on a pure-CPU bench) and each fit is its OWN VM, so concurrent
# fits are safe too.  So under Apple `container` we keep the same wide defaults
# as native x86_64 Linux — which also matches the processor count the committed
# seeds were tuned at on Linux.  Override with `QPROCS=N MAXJOBS=N make build`.
ifeq ($(shell uname -s)/$(shell uname -m),Darwin/arm64)
ifeq ($(OCI),container)
ROSETTA := 0
else
ROSETTA := 1
endif
else
ROSETTA := 0
endif

# Parallel-fit width for build-all and sweep.  Containers isolate the shared
# Quartus state (private $HOME + /tmp), so N fits run safely at once.
MAXJOBS ?= $(if $(filter 1,$(ROSETTA)),1,4)
# Fitter processor count, pinned into every generated qsf (NUM_PARALLEL_PROCESSORS)
# so builds are reproducible and a sweep fit matches the eventual production build
# (Quartus's parallel placement varies with processor count).  QPROCS x MAXJOBS
# should fit the host cores (native / Apple `container`: 4 x 4 = 16;
# Docker-on-Apple-Silicon: 1 x 1 to avoid Rosetta's 112002).
QPROCS ?= $(if $(filter 1,$(ROSETTA)),1,4)

# ── Build ALL variants — concurrent isolated container fits ──────────
# Fans out over $(VARIANTS); each builds in its OWN container + dir (bld/<v>),
# so fits run AT THE SAME TIME with zero interference (the container isolation
# is what makes this safe — concurrent host fits segfault).  xargs -P caps
# concurrency at $(MAXJOBS), so adding variants past the host width queues
# instead of oversubscribing.  CPU netlists are generated sequentially first
# (the sbt generator can't run concurrently); logs go to bld/<v>-build.log.
.PHONY: build-all
build-all:
	@for v in $(VARIANTS); do $(MAKE) --no-print-directory cpu VARIANT=$$v; done
	@$(MAKE) --no-print-directory bootloader
	@for v in $(VARIANTS); do $(MAKE) --no-print-directory bld/$$v/ap_core.qsf VARIANT=$$v; done
	@printf "$(C_HEAD)[fpga]$(C_RESET) Parallel container build: $(VARIANTS) ($(MAXJOBS)-wide)...\n"
	@rm -f bld/.build-all-fail
	@printf "%s\n" $(VARIANTS) | xargs -P $(MAXJOBS) -I{} sh -c \
	  'bash $(ROOT_DIR)/tools/quartus-container.sh "$(CURDIR)/bld/{}" > bld/{}-build.log 2>&1 || echo {} >> bld/.build-all-fail'
	@for v in $(VARIANTS); do \
	  if grep -qx $$v bld/.build-all-fail 2>/dev/null; \
	  then printf "  $(C_ERR)%s FAILED$(C_RESET) (bld/%s-build.log)\n" "$$v" "$$v"; \
	  else printf "  $(C_OK)%s rc=0$(C_RESET) (bld/%s-build.log)\n" "$$v" "$$v"; fi; done
	@if [ -s bld/.build-all-fail ]; then exit 1; fi
	@for v in $(VARIANTS); do $(MAKE) --no-print-directory install-bitstream VARIANT=$$v; done
	@printf "$(C_OK)[fpga] Built all variants: $(VARIANTS)$(C_RESET)\n"

# ── Seed-sweep ALL variants (sequential; each sweep is itself MAXJOBS-wide) ──
.PHONY: sweep-all
sweep-all:
	@for v in $(VARIANTS); do $(MAKE) --no-print-directory sweep VARIANT=$$v; done

# ── Test ─────────────────────────────────────────────────────────────
test:
	@printf "$(C_HEAD)[test]$(C_RESET) Running Verilator tests...\n"
	@$(MAKE) -C $(TEST_DIR) all
	@printf "$(C_OK)[test] Done$(C_RESET)\n"

# ── RTL check ────────────────────────────────────────────────────────
# Quick map-only sanity check (no fit / asm / sta).  Pairs with
# VARIANT_DEFS for optional build-flag checks.
check: $(BDIR)/ap_core.qsf
	@printf "$(C_HEAD)[check]$(C_RESET) Analysis & Synthesis (VARIANT=$(VARIANT))...\n"
	@cd $(BDIR) && quartus_map $(PROJECT)
	@printf "$(C_OK)[check] Done$(C_RESET)\n"

# ── Deploy ───────────────────────────────────────────────────────────
# `deploy` is now an explicit no-op that just refreshes both artifacts
# in build/ from whatever is currently on disk. Mostly useful as a
# top-level "make sure build/ matches the source tree" command. The
# individual build targets (os, build, firmware) already install their
# own outputs as a post-step, so you only need this if you want to
# re-sync without rebuilding anything.
deploy: install-os install-bitstream
	@printf "$(C_OK)[deploy] Done$(C_RESET)\n"

# `flash` removed — `firmware` now does the same thing (it builds os,
# patches the .sof's MIF, and reinstalls the bitstream + os.bin to
# build/). The old `flash` was identical except for the recipe order.

# ── Package: APF SD-card tree → build/pocket/ ────────────────────────
# Assembles the Analogue Pocket SD-card layout (Cores/Assets/Platforms)
# from the on-disk build artifacts.  Invoked by the root `make package`,
# which delegates to whichever target is selected — the root names no
# target, so this layout is entirely the Pocket target's concern.
DIST_DIR      = $(ROOT_DIR)/dist/core
PLATFORM_DIST = $(ROOT_DIR)/dist/platforms
CHIP32_LOADER = $(ROOT_DIR)/src/chip32/pocket/loader.bin
PKG_DIR       = $(ROOT_DIR)/build/pocket
JSON_FILES    = core.json video.json audio.json input.json data.json variants.json interact.json

define INSTALL_TEXT
openfpgaOS — Installation

1. Copy the contents of this folder to your SD card root (merge with existing)
2. Power on your Analogue Pocket → Cores → ThinkElastic.openfpgaOS

For game development, use the SDK: https://github.com/ThinkElastic/openfpgaOS-SDK
endef
export INSTALL_TEXT

package: $(REVERSE_BITS)
	@rm -rf $(PKG_DIR)
	@mkdir -p $(RELEASE_DIR) $(PKG_DIR)/Platforms/_images $(ASSETS_DIR)
	@$(REVERSE_BITS) $(QOUT)/$(PROJECT).rbf $(RELEASE_DIR)/$(BITSTREAM).rbf_r > /dev/null
	@# chip32 loader is a small assembled artifact.  Invoke its sub-make
	@# UNCONDITIONALLY so bass reassembles whenever loader.asm (or the .arch)
	@# changed: a `test -f` existence guard skips the prerequisite check and
	@# would ship a stale binary (e.g. a pre-`=os30`-scan loader.bin that
	@# always selects core 0 / os25).  The chip32 Makefile no-ops when fresh.
	@$(MAKE) -s -C $(ROOT_DIR)/src/chip32/pocket
	@cp $(CHIP32_LOADER) $(RELEASE_DIR)/loader.bin
	@cp $(FW_DIR)/os.bin $(RELEASE_DIR)/os.bin
	@cp $(FW_DIR)/os.bin $(ASSETS_DIR)/os.bin
	@# core.json's bitstream "filename" must match the variant we just
	@# reversed; the rest of the manifests copy verbatim.
	@for f in $(JSON_FILES); do \
		if [ "$$f" = "core.json" ]; then \
			sed 's/\("filename":[[:space:]]*"\)[^"]*\(\.rbf_r"\)/\1$(BITSTREAM)\2/' \
				$(DIST_DIR)/$$f > $(RELEASE_DIR)/$$f; \
		else cp $(DIST_DIR)/$$f $(RELEASE_DIR)/; fi; \
	done
	@cp $(PLATFORM_DIST)/*.json $(PKG_DIR)/Platforms/
	@cp $(PLATFORM_DIST)/_images/*.bin $(PKG_DIR)/Platforms/_images/
	@test -f $(DIST_DIR)/icon.bin && cp $(DIST_DIR)/icon.bin $(RELEASE_DIR)/ || true
	@echo "$$INSTALL_TEXT" > $(PKG_DIR)/INSTALL.txt
	@echo ""
	@printf "  $(C_OK)Package ready$(C_RESET) → build/pocket/\n"
	@echo ""
	@tree -L 4 $(PKG_DIR) 2>/dev/null || find $(PKG_DIR) -type f | sort

# ── SDK sync (delegates to top-level Makefile) ───────────────────────
# Lets you run `make sdk DEST=path/to/sdk` from this directory and
# have it forward to the openfpgaOS root, where the actual sync logic
# lives (it copies of_*.h into <DEST>/src/sdk/include and the runtime
# files into <DEST>/runtime).
sdk:
	@$(MAKE) -C $(ROOT_DIR) sdk DEST="$(DEST)"

# ── JTAG program ─────────────────────────────────────────────────────
program:
	@printf "$(C_HEAD)[jtag]$(C_RESET) Programming via USB Blaster...\n"
	@quartus_pgm -m jtag -o "p;$(QOUT)/$(PROJECT).sof"
	@printf "$(C_OK)[jtag] Done$(C_RESET)\n"

# ── Resource / timing report ─────────────────────────────────────────
# Summary of resource usage; FULL=true adds the top ALM-consuming
# entities and the 25 worst setup paths (re-runs quartus_sta).
report:
	@chmod +x $(TOOLS_DIR)/report.sh 2>/dev/null || true
	@BLD_DIR="$(CURDIR)/$(BDIR)" VARIANT="$(VARIANT)" TARGET="$(TARGET)" JOB="$(JOB)" \
		bash $(TOOLS_DIR)/report.sh $(PROJECT) $(if $(filter true,$(FULL)),full)

# ── Timing report ────────────────────────────────────────────────────
timing:
	@test -f $(QOUT)/$(PROJECT).sta.rpt || { printf "$(C_ERR)[timing] no STA report in $(QOUT) — run 'make build VARIANT=$(VARIANT)' first$(C_RESET)\n"; exit 1; }
	@printf "$(C_HEAD)Timing report$(C_RESET) $(C_DIM)(target=$(TARGET) variant=$(VARIANT) job=$(JOB) — $(QOUT))$(C_RESET)\n"
	@printf "$(C_HEAD)Fmax Summary (Slow 85°C):$(C_RESET)\n"
	@printf "  %-12s %-12s %s\n" "Achieved" "Target" "Clock"
	@printf "  %-12s %-12s %s\n" "--------" "------" "-----"
	@grep -E "^\; [0-9]" $(QOUT)/$(PROJECT).sta.rpt 2>/dev/null | head -6 | while IFS=';' read _ fmax rfmax clock _rest; do \
		fmax=$$(echo "$$fmax" | xargs); \
		clock=$$(echo "$$clock" | xargs); \
		short=$$(echo "$$clock" | sed 's/.*mp_ram.*general\[\([0-9]\)\].*/ram_out\1/' | sed 's/.*mp1.*general\[\([0-9]\)\].*/pll_out\1/' | sed 's/.*bridge_spiclk/spi/' | sed 's/.*clk_74a/clk_74a/'); \
		case "$$short" in \
			ram_out[0-2]) target="100.00 MHz" ;; \
			pll_out[01])  target="12.29 MHz" ;; \
			pll_out2)     target="49.15 MHz" ;; \
			pll_out[34])  target="12.57 MHz" ;; \
			clk_74a|spi)  target="74.25 MHz" ;; \
			*)            target="—" ;; \
		esac; \
		printf "  %-12s %-12s %s\n" "$$fmax" "$$target" "$$short"; \
	done
	@echo ""
	@printf "$(C_HEAD)Setup Slack (Slow 85°C, worst per clock):$(C_RESET)\n"
	@grep -A20 "Slow 1100mV 85C Model Setup Summary" $(QOUT)/$(PROJECT).sta.rpt 2>/dev/null \
		| grep "^;" | grep -E "\-?[0-9]+\.[0-9]+" \
		| awk -F';' '{ \
			clock=$$2; gsub(/^[ \t]+|[ \t]+$$/, "", clock); \
			slack=$$3; gsub(/^[ \t]+|[ \t]+$$/, "", slack); \
			tns=$$4; gsub(/^[ \t]+|[ \t]+$$/, "", tns); \
			if (!(clock in seen) || slack+0 < seen[clock]+0) { seen[clock]=slack; tns_map[clock]=tns; } \
		} END { \
			for (c in seen) print seen[c], tns_map[c], c; \
		}' | sort -n | while read slack tns clock; do \
		short=$$(echo "$$clock" | sed 's/.*mp_ram.*general\[\([0-9]\)\].*/ram_out\1/' | sed 's/.*mp1.*general\[\([0-9]\)\].*/pll_out\1/' | sed 's/.*bridge_spiclk/spi/' | sed 's/.*clk_74a/clk_74a/' | sed 's/.*cram0_clk_pin/cram0/' | sed 's/.*dram_clk_pin/dram/'); \
		if echo "$$slack" | grep -q "^-"; then \
			printf "  $(C_ERR)%-10s$(C_RESET) TNS %-12s %s\n" "$$slack" "$$tns" "$$short"; \
		else \
			printf "  $(C_OK)%-10s$(C_RESET) TNS %-12s %s\n" "$$slack" "$$tns" "$$short"; \
		fi; \
	done

# ── Build summary ────────────────────────────────────────────────────
summary:
	@echo ""
	@printf "$(C_HEAD)Build Summary$(C_RESET)\n"
	@test -f $(VEXII_OUT) \
		&& printf "  $(C_OK)[x]$(C_RESET) CPU generated\n" \
		|| printf "  $(C_DIM)[ ] CPU$(C_RESET)\n"
	@test -f $(FW_DIR)/firmware.mif \
		&& printf "  $(C_OK)[x]$(C_RESET) Firmware built\n" \
		|| printf "  $(C_DIM)[ ] Firmware$(C_RESET)\n"
	@test -f $(FW_DIR)/os.bin \
		&& SIZE=$$(stat -c%s $(FW_DIR)/os.bin 2>/dev/null || stat -f%z $(FW_DIR)/os.bin 2>/dev/null) \
		&& printf "  $(C_OK)[x]$(C_RESET) OS built ($$SIZE bytes)\n" \
		|| printf "  $(C_DIM)[ ] OS$(C_RESET)\n"
	@if test -f $(QOUT)/$(PROJECT).rbf; then \
		FMAX=$$(grep -E "^\; [0-9]" $(QOUT)/$(PROJECT).sta.rpt 2>/dev/null \
			| grep "mp_ram.*general\[0\]" | head -1 \
			| awk -F';' '{print $$2}' | xargs); \
		if [ -n "$$FMAX" ]; then \
			printf "  $(C_OK)[x]$(C_RESET) FPGA compiled $(C_DIM)($$FMAX)$(C_RESET)\n"; \
		else \
			printf "  $(C_OK)[x]$(C_RESET) FPGA compiled\n"; \
		fi; \
	else \
		printf "  $(C_DIM)[ ] FPGA$(C_RESET)\n"; \
	fi
	@test -f $(RELEASE_DIR)/$(BITSTREAM).rbf_r \
		&& printf "  $(C_OK)[x]$(C_RESET) Deployed ($(BITSTREAM).rbf_r)\n" \
		|| printf "  $(C_DIM)[ ] Deploy$(C_RESET)\n"
	@echo ""

# ── Seed sweep ───────────────────────────────────────────────────────
SWEEP_MIN ?= 1
SWEEP_MAX ?= 30

# MAXJOBS-wide parallel container seed search for the selected VARIANT.  Each
# seed fits in its own isolated container + bld/<variant>-s<seed>/ dir, so the
# sweep runs $(MAXJOBS) fits at once; the winner is written into the variant's
# stored seed file (seeds/<variant>.seed) so the next `make build VARIANT=<v>`
# uses it.  E.g. `make sweep VARIANT=os30 SWEEP_MIN=1 SWEEP_MAX=12 MAXJOBS=4`.
sweep:
	@MAXJOBS=$(MAXJOBS) VARIANT=$(VARIANT) PROJECT=$(PROJECT) \
		CLOCK_RE='mp_ram.*general\[0\]' SEED_FILE="$(CURDIR)/$(SEED_FILE)" \
		TARGET_DIR="$(CURDIR)" \
		bash $(ROOT_DIR)/tools/sweep.sh $(SWEEP_MIN) $(SWEEP_MAX)

# ── Tools ────────────────────────────────────────────────────────────
$(REVERSE_BITS): $(TOOLS_DIR)/reverse_bits.c
	@gcc -O2 -o $@ $<

# ── Clean ────────────────────────────────────────────────────────────
clean:
	@printf "$(C_HEAD)[clean]$(C_RESET) Removing build artifacts...\n"
	@rm -rf bld
	@# Legacy in-place artifacts from the pre-bld/ build scheme, if any remain.
	@rm -rf db incremental_db output_files
	@rm -f sweep_s*.qsf
	@$(FW_MAKE) -s clean
	@rm -f $(VEXII_DIR)/VexiiRiscv_*.v

# Alias for root Makefile compatibility
clean-all: clean

.PHONY: all full help cpu bootloader firmware os compile build test check deploy sdk program report package
.PHONY: install-os install-bitstream timing summary sweep clean clean-all
