#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# Super Mario 64 — openfpgaOS custom core
#
# A standalone openFPGA core wrapping the SM64 PC-port engine, rendering
# through the os30 hardware vertex-triangle GPU (of_gpu.h).  Builds a
# single app.elf that the openfpgaOS ELF loader runs on the shared os30
# bitstream.
#
# Targets:
#   make           Build the core (app.elf + SD tree under build/pocket/sm64/)
#   make copy      Copy this core to your own Pocket SD card
#   make assets    Extract + generate the ROM assets only
#   make test      Desktop SDL2 smoke build (app_pc)
#   make clean     Remove build artifacts
#   make distclean Also remove the generated asset tree
#   make assets-clean  Also remove every ROM-extracted asset (keeps your ROM)
#
# There is deliberately no `make package` — this core is SOURCE ONLY.
# You must supply your own Super Mario 64 (U) ROM; see DISTRIBUTION.md.

APP       = sm64
TARGET   ?= pocket
MISTER_IP ?= mister.local
SDK_DIR   = ../sdk
ROOT      = $(realpath $(CURDIR)/../..)
OBJ_DIR   = $(ROOT)/.obj/$(APP)$(if $(filter 1,$(PROFILE)),-profile)
BUILD_DIR = $(OBJ_DIR)
DIST      = $(ROOT)/dist/$(APP)
OUT       = $(ROOT)/build/$(TARGET)/$(APP)
RUNTIME   = $(ROOT)/runtime

# ── SM64 source tree (moved under src/sm64/sm64) ─────────────────────
SM64   = sm64
SRC    = $(SM64)/src
BUILD  = $(SM64)/build/us_pocket

# Assets are extracted from the user's own SM64 ROM at build time and
# compiled into the ELF; the ELF is self-contained.  No ROM data — and
# nothing derived from a ROM — is distributed with this project.
ASSET_BLOBS ?=
PROFILE ?= 0
# Low-overhead hardware snapshots, opened with Select+L+R. The detailed UART
# profiler remains separate: its per-triangle timers perturb GPU traffic.
PERF_DIAG ?= 1
ifeq ($(PROFILE),1)
override PERF_DIAG := 0
endif
GEOMETRY ?= 1
GEOMETRY_REFERENCE ?= 0
ifeq ($(GEOMETRY_REFERENCE),1)
OBJ_DIR := $(ROOT)/.obj/sm64-geometry-reference
endif
ifeq ($(GEOMETRY),0)
OBJ_DIR := $(ROOT)/.obj/sm64-geometry-off
endif
ifeq ($(PERF_DIAG),1)
OBJ_DIR := $(OBJ_DIR)-perfdiag
endif

# ── Asset extraction (baserom.us.z64 → PNG / AIFF / M64 / BIN) ────────
# GNU make expands every $(wildcard ...) below while it PARSES this file, so
# the decomp's extracted assets and its host tools must exist BEFORE that
# point — an ordinary rule runs far too late.  Upstream does the same thing
# the same way (sm64/Makefile:206 and :213).
#
#   DO NOT move any $(wildcard) above this block.
#   DO NOT convert this block into a rule.
#
# Skipped inside the SDK container (sdk.mk re-parses this file there; the
# host pass has already extracted, and the firmware image has no host cc)
# and for the clean targets.
SM64_BASEROM    := $(SM64)/baserom.us.z64
# Only the tools this core actually uses.  Naming them as goals skips
# tools/Makefile's default `all`, which would also build armips (a 616 KB C++
# TU) and audiofile/libaudiofile.a for tabledesign — neither is needed here.
SM64_HOST_TOOLS := n64graphics skyconv textconv aiff_extract_codebook \
                   vadpcm_enc mio0 aifc_decode

SM64_NOEXTRACT_GOALS := clean distclean sdk-clean assets-clean

ifneq ($(OF_SDK_IN_CONTAINER),1)
ifeq ($(filter $(SM64_NOEXTRACT_GOALS),$(MAKECMDGOALS)),)

ifeq ($(wildcard $(SM64_BASEROM)),)
$(error Missing $(SM64_BASEROM) -- place your own unmodified US Super Mario 64 ROM there (big-endian .z64, SHA-1 9bef1128717f958171a4afac3ed78ee2bb4e86ce). No ROM data is distributed with this project; see the README)
endif

# MAKEFLAGS= keeps the sub-make out of the jobserver: it runs from a shell
# assignment, not a recipe, so it has no jobserver file descriptors.
_SM64_TOOLS != MAKEFLAGS= $(MAKE) -s -C $(SM64)/tools $(SM64_HOST_TOOLS) >&2 || echo FAIL
ifeq ($(_SM64_TOOLS),FAIL)
$(error Failed to build the SM64 host tools in $(SM64)/tools -- a host cc and make are required)
endif

_SM64_ASSETS != cd $(SM64) && ./extract_assets.py us >&2 || echo FAIL
ifeq ($(_SM64_ASSETS),FAIL)
$(error Asset extraction failed -- check that $(SM64_BASEROM) is an unmodified US ROM)
endif

endif
endif

# ── Sources ──────────────────────────────────────────────────────────
ENGINE_SRCS  = $(wildcard $(SRC)/engine/*.c)
GAME_SRCS    = $(filter-out $(SRC)/game/main.c, $(wildcard $(SRC)/game/*.c))
AUDIO_SRCS   = $(wildcard $(SRC)/audio/*.c)
MENU_SRCS    = $(wildcard $(SRC)/menu/*.c)
BUFFER_SRCS  = $(wildcard $(SRC)/buffers/*.c)
GODDARD_SRCS = $(wildcard $(SRC)/goddard/*.c) $(wildcard $(SRC)/goddard/dynlists/*.c)

# PC-port layer (selected) + the new openfpgaOS GPU renderer
PC_SRCS = $(SRC)/pc/pc_main.c \
          $(SRC)/pc/ultra_reimplementation.c \
          $(SRC)/pc/configfile.c \
          $(SRC)/pc/mixer.c \
          $(SRC)/pc/of_voice.c \
          $(SRC)/pc/controller/controller_entry_point.c \
          $(SRC)/pc/gfx/gfx_pc.c \
          $(SRC)/pc/gfx/gfx_cc.c \
          $(SRC)/pc/gfx/gfx_gpu.c \
          $(SRC)/pc/gfx/async_mesh_repair.c

# libultra reimplementation (non-N64 float subset)
ULTRA_SRCS = $(SM64)/lib/src/alBnkfNew.c \
             $(SM64)/lib/src/guLookAtRef.c \
             $(SM64)/lib/src/guMtxF2L.c \
             $(SM64)/lib/src/guNormalize.c \
             $(SM64)/lib/src/guOrthoF.c \
             $(SM64)/lib/src/guPerspectiveF.c \
             $(SM64)/lib/src/guRotateF.c \
             $(SM64)/lib/src/guScaleF.c \
             $(SM64)/lib/src/guTranslateF.c

LEVEL_SRCS = $(wildcard $(SM64)/levels/*.c) \
             $(wildcard $(SM64)/levels/*/leveldata.c) \
             $(wildcard $(SM64)/levels/*/script.c) \
             $(wildcard $(SM64)/levels/*/geo.c)
ACTOR_SRCS = $(wildcard $(SM64)/actors/*.c)
BIN_SRCS   = $(wildcard $(SM64)/bin/*.c) $(wildcard $(SM64)/bin/us/*.c)
DATA_SRCS  = $(wildcard $(SM64)/data/*.c)
ASSET_SRCS = $(wildcard $(SM64)/assets/*.c)
# Sound banks (sequences + sample banks).  sound_data.c #includes the four
# generated .inc.c blobs (sound_data.ctl/.tbl, sequences.bin, bank_sets) out
# of $(BUILD)/sound/ — see the "Sound data generation" rules below.
SOUND_SRCS = $(SM64)/sound/sound_data.c

# Skybox .c files are emitted by skyconv, one per textures/skyboxes/*.png.
# Do NOT $(wildcard $(BUILD)/bin/*_skybox.c) — that wildcards the OUTPUT
# directory, so it is only ever non-empty on a tree that has already been
# built once, and can never bootstrap.  Derive from the PNGs instead, as
# upstream does (sm64/Makefile:329-330).
SKYBOX_PNGS = $(wildcard $(SM64)/textures/skyboxes/*.png)
SKYBOX_SRCS = $(patsubst $(SM64)/textures/skyboxes/%.png,$(BUILD)/bin/%_skybox.c,$(SKYBOX_PNGS))

# src/game/skybox.c hard-references all ten *_skybox_ptrlist symbols in
# sSkyboxTextures[10], so a short list is a link failure with ten undefined
# references and no hint that the cause is incomplete asset extraction.
ifeq ($(filter $(SM64_NOEXTRACT_GOALS),$(MAKECMDGOALS)),)
ifneq ($(words $(SKYBOX_SRCS)),10)
$(error Found $(words $(SKYBOX_SRCS)) skybox PNGs under $(SM64)/textures/skyboxes, expected 10 -- asset extraction is incomplete)
endif
endif

GENERATED_SRCS = $(BUILD)/assets/mario_anim_data.c \
                 $(BUILD)/assets/demo_data.c \
                 $(SKYBOX_SRCS)

# openfpgaOS platform backends (replace the raw-MMIO pocket/ layer)
OF_SRCS = pocket/main_of.c \
          pocket/wm_pocket.c \
          pocket/controller_pocket.c \
          pocket/audio_pocket.c \
          pocket/fx32_mtx.c \
          pocket/perf_pocket.c

SRCS = $(ENGINE_SRCS) $(GAME_SRCS) $(AUDIO_SRCS) $(MENU_SRCS) $(BUFFER_SRCS) \
       $(GODDARD_SRCS) $(PC_SRCS) $(ULTRA_SRCS) $(LEVEL_SRCS) $(ACTOR_SRCS) \
       $(BIN_SRCS) $(DATA_SRCS) $(ASSET_SRCS) $(GENERATED_SRCS) $(SOUND_SRCS) \
       $(OF_SRCS)
SRCS_CXX =

# ── Compiler flags ───────────────────────────────────────────────────
# Include search: SM64 engine headers + generated build dir + app root
# (so "pocket/..." resolves).  musl/of_* headers come from sdk.mk.
CFLAGS  = -I. -I$(SM64)/include -I$(SRC) -I$(SRC)/pc -I$(SM64) \
          -I$(BUILD) -I$(BUILD)/include
CFLAGS += -std=gnu99 -fcommon -fno-strict-aliasing -fwrapv
# Performance: the engine is CPU-bound (display-list interp + transform +
# emit), so optimize aggressively.  rv32imafc has hardware single-float but
# no double, so keep all FP single (-fsingle-precision-constant) and let
# -ffast-math + -fno-math-errno inline lrintf/sqrtf to fcvt/fsqrt.s.
# Override the SDK's -falign-loops=64 (it padded ~9% of .text with NOPs on
# loops that run once per call) back to 8 — last wins.
# NOTE: -flto is intentionally OFF — the upstream SM64 decomp has benign
# cross-TU display-list type mismatches (-Wlto-type-mismatch) and the
# original Makefile disabled LTO; enable it only after a hardware soak.
CFLAGS += -O3 -ffast-math -fno-math-errno -fno-trapping-math
CFLAGS += -fsingle-precision-constant
CFLAGS += -falign-loops=8
CFLAGS += -DSM64_PROFILE=$(PROFILE) -DSM64_PERF_DIAG=$(PERF_DIAG)
CFLAGS += -DGFX_GPU_GEOMETRY=$(GEOMETRY) -DGFX_GPU_GEOMETRY_REFERENCE=$(GEOMETRY_REFERENCE)
CFLAGS += -DVERSION_US -DNON_MATCHING -DAVOID_UB -D_LANGUAGE_C \
          -DF3DEX_GBI_2E -DNO_SEGMENTED_MEMORY -DWIDESCREEN=0 \
          -DENABLE_SOFTRAST -DTARGET_OPENFPGA
# SM64 PC-port is warning-noisy; keep the build readable.
CFLAGS += -Wno-unused-parameter -Wno-unused-variable -Wno-unused-function \
          -Wno-missing-field-initializers -Wno-sign-compare \
          -Wno-implicit-function-declaration -Wno-pointer-sign \
          -Wno-parentheses -Wno-unused-but-set-variable \
          -Wno-maybe-uninitialized -Wno-format
# Upstream SM64 decomp noise that only surfaces at -O3 (benign): missing
# returns on unreachable switch defaults, intentional uninit reads, ROM-DMA
# memcpy range false-positives.
CFLAGS += -Wno-return-type -Wno-uninitialized -Wno-implicit-fallthrough \
          -Wno-stringop-overread

.DEFAULT_GOAL := all

include $(SDK_DIR)/sdk.mk

# Track renderer ABI/header changes and compiler flags locally. The vendored
# SDK's object rules otherwise only depend on the C source timestamp.
SDK_CFLAGS += -MMD -MP
# Schedule the unchanged RV32 instruction set for a single-issue pipeline.
SDK_CFLAGS += -mtune=sifive-5-series
$(APP_OBJS) $(OF_INIT_OBJ) $(OF_SDL2_OBJ): Makefile
# Frozen edge profiles apply only to the qualified normal Pocket renderer.
# GCC hashes its auxiliary filename into static-function profile IDs. Preserve
# this original identity only as an opaque lookup key below the local pgo tree;
# -MF keeps dependency outputs local. No file at the key's absolute path is used.
ifeq ($(PROFILE)$(PERF_DIAG)$(GEOMETRY)$(GEOMETRY_REFERENCE),0010)
SM64_RENDERER_PGO_DIR := $(CURDIR)/pgo
SM64_RENDERER_PGO_KEY := /home/alberto/Repos/openfpgaCore/docs/measurements/os30-fps25-20261005/phase10/renderer-pgo-v1/work/.obj/sm64/sm64/src/pc/gfx/
SM64_RENDERER_PGO_OBJS := $(foreach name,gfx_pc gfx_gpu,$(call sdk_app_object,$(SRC)/pc/gfx/$(name).c))
$(SM64_RENDERER_PGO_OBJS): CFLAGS += -fprofile-use=$(SM64_RENDERER_PGO_DIR) -dumpdir $(SM64_RENDERER_PGO_KEY) -MF $(@:.o=.d) -fprofile-partial-training -Werror=coverage-mismatch -Werror=missing-profile -fdump-ipa-profile-details=$(@:.o=.profile.txt)
$(SM64_RENDERER_PGO_OBJS): $(foreach name,gfx_pc gfx_gpu,$(SM64_RENDERER_PGO_DIR)$(SM64_RENDERER_PGO_KEY)$(name).gcda)
endif

# Goddard's float sine/cosine kernel needs strict IEEE evaluation: its
# two-step range reduction must not be reassociated, and nothing may fuse
# into the CPU's reduced-accuracy FMA (see gd_trig_openfpga.c).
$(call sdk_app_object,$(SRC)/goddard/gd_trig_openfpga.c): CFLAGS += -fno-fast-math -ffp-contract=off
$(call sdk_app_object,$(SRC)/pc/gfx/async_mesh_repair.c): CFLAGS += -fno-fast-math -ffp-contract=off
-include $(patsubst %.o,%.d,$(APP_OBJS) $(OF_INIT_OBJ) $(OF_SDL2_OBJ))

# ── Sound data generation ────────────────────────────────────────────
# Mirror the upstream SM64 audio pipeline to produce the four sound blobs
# that sound_data.c #includes.  The RISC-V app target is little-endian and
# 32-bit, so the N64 sound structures must be assembled with matching word
# size (--endian little --bitwidth 32) — a 64-bit layout makes the ALSeqData
# pointer fields the wrong size and the engine reads garbage offsets.
SOUND_DIR        := $(SM64)/sound
SOUND_BIN_DIR    := $(BUILD)/sound
SOUND_ENDIAN     := --endian little --bitwidth 32
SOUND_BANK_FILES := $(wildcard $(SOUND_DIR)/sound_banks/*.json)
SOUND_SAMPLE_AIFFS := $(wildcard $(SOUND_DIR)/samples/*/*.aiff)
SOUND_SAMPLE_AIFCS := $(patsubst $(SOUND_DIR)/%.aiff,$(BUILD)/sound/%.aifc,$(SOUND_SAMPLE_AIFFS))
# Sequence files: prebuilt us/*.m64 plus 00_sound_player.s assembled to .m64.
SOUND_SEQ_M64    := $(wildcard $(SOUND_DIR)/sequences/us/*.m64) \
                    $(patsubst $(SOUND_DIR)/sequences/%.s,$(BUILD)/sound/sequences/%.m64,\
                        $(wildcard $(SOUND_DIR)/sequences/*.s))
SOUND_INC_FILES  := $(SOUND_BIN_DIR)/sound_data.ctl.inc.c \
                    $(SOUND_BIN_DIR)/sound_data.tbl.inc.c \
                    $(SOUND_BIN_DIR)/sequences.bin.inc.c \
                    $(SOUND_BIN_DIR)/bank_sets.inc.c

PYTHON   ?= python3
AIFF_EXTRACT_CODEBOOK := $(SM64)/tools/aiff_extract_codebook
VADPCM_ENC            := $(SM64)/tools/vadpcm_enc
ASSEMBLE_SOUND        := $(SM64)/tools/assemble_sound.py
N64GRAPHICS           := $(SM64)/tools/n64graphics
SKYCONV               := $(SM64)/tools/skyconv
TEXTCONV              := $(SM64)/tools/textconv
# Host preprocessor for the text tables and level_headers.h.  Upstream writes
# $(CPP), which under its TARGET_DOS ?= 1 default (sm64/Makefile:25) expands
# to i586-pc-msdosdjgpp-cpp (sm64/Makefile:490) — name the host one
# explicitly.  -P matches upstream's own Linux definition (sm64/Makefile:485).
HOST_CPP              ?= cpp -P
# Sequence .s files are pure data directives (the seq_* macros emit only
# .byte/.half/.word), so the host assembler produces byte-identical .m64
# output to any cross target — use it to stay independent of the RISC-V
# toolchain prefix for this step.
HOST_AS      ?= as
HOST_OBJCOPY ?= objcopy

# .aiff -> .table -> .aifc (ADPCM-encoded sample)
$(BUILD)/sound/%.table: $(SOUND_DIR)/%.aiff
	@mkdir -p $(dir $@)
	$(AIFF_EXTRACT_CODEBOOK) $< > $@

$(BUILD)/sound/%.aifc: $(BUILD)/sound/%.table $(SOUND_DIR)/%.aiff
	$(VADPCM_ENC) -c $^ $@

# 00_sound_player.s (sequence bytecode, host-assembler safe: data only) -> .m64
$(BUILD)/sound/sequences/%.m64: $(SOUND_DIR)/sequences/%.s
	@mkdir -p $(dir $@)
	$(HOST_AS) -I $(SM64)/include -I $(BUILD) --defsym VERSION_US=1 -o $@.o $<
	$(HOST_OBJCOPY) -j .rodata $@.o -O binary $@

# Sample banks -> sound_data.ctl / .tbl
$(SOUND_BIN_DIR)/sound_data.ctl: $(SOUND_BANK_FILES) $(SOUND_SAMPLE_AIFCS) $(ASSEMBLE_SOUND)
	@mkdir -p $(dir $@)
	$(PYTHON) $(ASSEMBLE_SOUND) $(BUILD)/sound/samples/ $(SOUND_DIR)/sound_banks/ \
		$(SOUND_BIN_DIR)/sound_data.ctl $(SOUND_BIN_DIR)/sound_data.tbl \
		-DVERSION_US $(SOUND_ENDIAN)
$(SOUND_BIN_DIR)/sound_data.tbl: $(SOUND_BIN_DIR)/sound_data.ctl ;

# Sequences + bank_sets
$(SOUND_BIN_DIR)/sequences.bin: $(SOUND_BANK_FILES) $(SOUND_DIR)/sequences.json $(SOUND_SEQ_M64) $(ASSEMBLE_SOUND)
	@mkdir -p $(dir $@)
	$(PYTHON) $(ASSEMBLE_SOUND) --sequences $@ $(SOUND_BIN_DIR)/bank_sets \
		$(SOUND_DIR)/sound_banks/ $(SOUND_DIR)/sequences.json $(SOUND_SEQ_M64) \
		-DVERSION_US $(SOUND_ENDIAN)
$(SOUND_BIN_DIR)/bank_sets: $(SOUND_BIN_DIR)/sequences.bin ;

# Binary blob -> C byte array
$(SOUND_BIN_DIR)/%.inc.c: $(SOUND_BIN_DIR)/%
	hexdump -v -e '1/1 "0x%X,"' $< > $@
	echo >> $@

# sound_data.c pulls the blobs in via #include; force the right build order.
$(OBJ_DIR)/$(SM64)/sound/sound_data.o: $(SOUND_INC_FILES)

.PHONY: sound
sound: $(SOUND_INC_FILES)

# ── Generated asset sources (baserom → $(BUILD)) ─────────────────────
# Ported from the upstream decomp rules rather than delegating to
# sm64/Makefile with a BUILD_DIR override.  That override does work, but the
# upstream Makefile defaults to TARGET_DOS=1 + COMPILER=ido (sm64/Makefile:25,
# 27), so its text and level_headers rules invoke i586-pc-msdosdjgpp-cpp
# (sm64/Makefile:490); and it derives the sound bank word size from the *host*
# compiler (sm64/Makefile:775-793) — 64 bits here, which would silently break
# the ALSeqData layout the rules above deliberately pin to 32.  It also has no
# `assets` target to hook, so delegating would mean patching the vendored tree.
#
# skyboxes go through skyconv; ipl3_raw is N64-boot only and nothing here
# includes it.  No .ci8/.ci4 textures exist in the US extraction, so
# n64graphics_ci is not needed.
TEXTURE_PNGS = $(filter-out $(SM64)/textures/skyboxes/% $(SM64)/textures/ipl3_raw/%, \
                   $(wildcard $(SM64)/textures/*/*.png)) \
               $(wildcard $(SM64)/actors/*/*.png) \
               $(wildcard $(SM64)/levels/*/*.png)
TEXTURE_INCS = $(patsubst $(SM64)/%.png,$(BUILD)/%.inc.c,$(TEXTURE_PNGS))

# NOTE: the host tools ($(N64GRAPHICS), $(SKYCONV), $(TEXTCONV)) are
# deliberately NOT listed as prerequisites of any rule here.  They have no rule
# in this Makefile, so make would fall back to its builtin `%: %.c` and rebuild
# them with $(CC) — the RISC-V cross compiler — producing binaries this host
# cannot execute.  The parse-time `make -C $(SM64)/tools` above already
# guarantees they exist and are up to date.

# PNG -> raw N64 texture; the format is the last dot-suffix of the target.
# (upstream sm64/Makefile:715-716)
$(BUILD)/%: $(SM64)/%.png
	@mkdir -p $(dir $@)
	$(N64GRAPHICS) -i $@ -g $< -f $(lastword $(subst ., ,$@))

# raw texture -> C byte array (upstream sm64/Makefile:718-720)
$(BUILD)/%.inc.c: $(BUILD)/% $(SM64)/%.png
	@mkdir -p $(dir $@)
	hexdump -v -e '1/1 "0x%X,"' $< > $@
	echo >> $@

# skyconv emits $(BUILD)/bin/<name>_skybox.c, a standalone TU defining
# <name>_skybox_ptrlist (upstream Makefile.split:237-238).
$(BUILD)/bin/%_skybox.c: $(SM64)/textures/skyboxes/%.png
	@mkdir -p $(dir $@)
	$(SKYCONV) --type sky --split $< $(BUILD)/bin

# The ending cake is tiled by skyconv, not n64graphics.  This explicit rule is
# REQUIRED: cake.png is matched by $(wildcard $(SM64)/levels/*/*.png), so
# without it the generic pattern above would run n64graphics with -f set to
# the whole dotless path and fail.  Explicit rules beat pattern rules.
# (upstream Makefile.split:169-170; levels/ending/leveldata.c does the include)
$(BUILD)/levels/ending/cake.inc.c: $(SM64)/levels/ending/cake.png
	@mkdir -p $(dir $@)
	$(SKYCONV) --type cake --split $< $(BUILD)/levels/ending

# Encoded text tables (upstream sm64/Makefile:658-662, 696-697, 705)
$(BUILD)/include/text_menu_strings.h: $(SM64)/include/text_menu_strings.h.in
	@mkdir -p $(dir $@)
	$(TEXTCONV) $(SM64)/charmap_menu.txt $< $@

$(BUILD)/include/text_strings.h: $(SM64)/include/text_strings.h.in \
                                 $(BUILD)/include/text_menu_strings.h
	@mkdir -p $(dir $@)
	$(TEXTCONV) $(SM64)/charmap.txt $< $@

# text/define_text.inc.c #includes define_courses.inc.c relative to its own
# directory, so text/define_courses.inc.c is required on the US path too — it
# only *looks* EU-only from levels/menu/leveldata.c.
$(BUILD)/text/us/define_text.inc.c: $(SM64)/text/define_text.inc.c \
                                    $(SM64)/text/define_courses.inc.c \
                                    $(SM64)/text/us/courses.h \
                                    $(SM64)/text/us/dialogs.h
	@mkdir -p $(dir $@)
	$(HOST_CPP) -DVERSION_US -I $(SM64)/text/us $< -o - | $(TEXTCONV) $(SM64)/charmap.txt - $@

# levels/scripts.c #includes this (upstream sm64/Makefile:811-814)
$(BUILD)/include/level_headers.h: $(SM64)/levels/level_headers.h.in \
                                  $(SM64)/levels/level_defines.h
	@mkdir -p $(dir $@)
	$(HOST_CPP) -I $(SM64) $< | $(PYTHON) $(SM64)/tools/output_level_headers.py > $@

# These two converters open assets/anims/ and assets/demos/ relative to the
# PROCESS CWD, so they must run from $(SM64).  $(abspath ...) is purely
# lexical in GNU make, so it is safe on a path that does not exist yet.
$(BUILD)/assets/mario_anim_data.c: $(wildcard $(SM64)/assets/anims/*.inc.c) \
                                   $(SM64)/tools/mario_anims_converter.py
	@mkdir -p $(dir $@)
	cd $(SM64) && $(PYTHON) tools/mario_anims_converter.py > $(abspath $@)

$(BUILD)/assets/demo_data.c: $(SM64)/assets/demo_data.json \
                             $(wildcard $(SM64)/assets/demos/*.bin) \
                             $(SM64)/tools/demo_data_converter.py
	@mkdir -p $(dir $@)
	cd $(SM64) && $(PYTHON) tools/demo_data_converter.py assets/demo_data.json -DVERSION_US > $(abspath $@)

# ── Generation ordering ──────────────────────────────────────────────
SM64_GENERATED = $(TEXTURE_INCS) $(SKYBOX_SRCS) \
                 $(BUILD)/include/level_headers.h \
                 $(BUILD)/include/text_strings.h \
                 $(BUILD)/include/text_menu_strings.h \
                 $(BUILD)/text/us/define_text.inc.c \
                 $(BUILD)/assets/mario_anim_data.c \
                 $(BUILD)/assets/demo_data.c \
                 $(SOUND_INC_FILES)

.PHONY: assets
assets: $(SM64_GENERATED)

# Almost every engine TU #includes one of these blobs.  Order-only: the assets
# derive from a fixed ROM, so regenerating one must not rebuild the world, but
# -jN must never start compiling before generation finishes.
$(APP_OBJS): | $(SM64_GENERATED)

# Pin generation to the HOST pass.  sdk.mk re-execs this Makefile inside the
# openfpgaos-firmware image, which has no host cc/as/objcopy, so the sound
# rules above cannot run there.  In the delegate branch app.elf carries no
# object prerequisites on the host (sdk.mk), and that comment names generated
# assets as the intended hook — so hang generation off app.elf itself rather
# than off sound_data.o, which is a container-side target.
$(OBJ_DIR)/app.elf: | $(SM64_GENERATED)
app_pc: | $(SM64_GENERATED)

# Build ELF, then assemble the SD-card image under build/<target>/<app>/
all: $(OBJ_DIR)/app.elf release
	@$(SIZE) $<

# NOTE: despite the name, this is LOCAL SD-tree assembly and a prerequisite of
# `all` — it is NOT a distribution step, and it is unrelated to the root
# Makefile's `release` target (the GitHub publisher).  Do not remove it.
release: $(OBJ_DIR)/app.elf
	@test -x $(SDK_DIR)/platforms/$(TARGET)/image.sh || { \
		echo "Error: no image.sh for TARGET=$(TARGET)"; exit 1; }
	@$(SDK_DIR)/platforms/$(TARGET)/image.sh "$(APP)" "$(OBJ_DIR)/app.elf" "$(ROOT)" "$(DIST)" $(ASSET_BLOBS)

debug: all
	@$(ROOT)/scripts/debug.sh $$(ls -d $(OUT)/Assets/*/common 2>/dev/null)/$(APP).elf

copy: all
	@MISTER_IP="$(MISTER_IP)" $(SDK_DIR)/platforms/$(TARGET)/copy.sh "$(APP)" "$(OBJ_DIR)/app.elf"

# ── Distribution: SOURCE ONLY ────────────────────────────────────────
# app.elf embeds assets extracted from the user's own Super Mario 64 ROM, and
# it links gfx_pc.c / gfx_cc.c, whose license forbids binary redistribution
# outright (sm64/src/pc/gfx/LICENSE.txt clause 2).  `make` and `make copy` are
# deliberately unaffected — building and deploying to YOUR OWN device is fine.
# Rationale and the full reasoning: DISTRIBUTION.md.
package:
	@printf 'refusing to package: this core is SOURCE ONLY.\n\n'
	@printf '  * app.elf embeds assets extracted from your Super Mario 64 ROM\n'
	@printf '    (Super Mario 64 is (c) 1996 Nintendo Co., Ltd.).\n'
	@printf '  * It links gfx_pc.c / gfx_cc.c, whose license states\n'
	@printf '    "Redistributions in binary form are not allowed"\n'
	@printf '    (sm64/src/pc/gfx/LICENSE.txt, clause 2).\n'
	@printf '  * The bundle would also carry the SC-55 sample bank and the\n'
	@printf '    prebuilt openfpgaOS bitstream, which have their own terms.\n\n'
	@printf '  Build it yourself instead:  make && make copy\n'
	@printf '  Rationale: DISTRIBUTION.md\n'
	@exit 1

test: app_pc

clean: sdk-clean
	rm -rf $(ROOT)/build/pocket/$(APP) $(ROOT)/build/mister/$(APP)

# Full wipe: also remove the SM64 decomp's generated tree (assets, level
# .inc.c, sound banks). `clean` is app-only and reuses these (they're slow to
# regenerate); use distclean to force a true from-scratch rebuild.
distclean: clean
	rm -rf $(SM64)/build

# True from-scratch state: also drop every ROM-extracted asset, so the next
# `make` re-runs extract_assets.py.  --clean is the decomp's own sanctioned
# removal path (it reads .assets-local.txt).  Your baserom.us.z64 is NOT
# deleted.
.PHONY: assets-clean
assets-clean: distclean
	cd $(SM64) && ./extract_assets.py --clean

.PHONY: all release debug copy package test clean distclean assets assets-clean
