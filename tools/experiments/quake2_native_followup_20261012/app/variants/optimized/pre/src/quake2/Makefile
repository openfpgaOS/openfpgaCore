# quake2 — yquake2 for openfpgaOS.
#
# The target build is fully static: client + server + baseq2 game +
# software renderer + OpenFPGAOS platform layer.  No Unix backend,
# dynamic game DLL, dynamic renderer, OpenGL, cURL, or OpenAL.

APP       = quake2
GPU_PROFILE ?= 0
ifneq ($(GPU_PROFILE),0)
ifneq ($(GPU_PROFILE),1)
$(error GPU_PROFILE must be 0 or 1)
endif
endif
TARGET   ?= pocket
MISTER_IP ?= mister.local
SDK_DIR   = ../sdk
ROOT      = $(realpath $(CURDIR)/../..)
OBJ_DIR   = $(ROOT)/.obj/$(APP)$(if $(filter 1,$(GPU_PROFILE)),-gpuprofile)
BUILD_DIR = $(OBJ_DIR)
DIST      = $(ROOT)/dist/$(APP)
OUT       = $(ROOT)/build/$(TARGET)/$(APP)
RUNTIME   = $(ROOT)/runtime
GAME_ASSETS = $(wildcard *.pak *.pk2 *.pk3 *.cfg *.cin *.pcx *.wal *.wav *.ogg *.dat *.png)

YQ2 := src

CLIENT_SRCS := \
	$(YQ2)/client/cl_cin.c \
	$(YQ2)/client/cl_console.c \
	$(YQ2)/client/cl_download.c \
	$(YQ2)/client/cl_effects.c \
	$(YQ2)/client/cl_entities.c \
	$(YQ2)/client/cl_image.c \
	$(YQ2)/client/cl_input.c \
	$(YQ2)/client/cl_inventory.c \
	$(YQ2)/client/cl_keyboard.c \
	$(YQ2)/client/cl_lights.c \
	$(YQ2)/client/cl_main.c \
	$(YQ2)/client/cl_network.c \
	$(YQ2)/client/cl_parse.c \
	$(YQ2)/client/cl_particles.c \
	$(YQ2)/client/cl_prediction.c \
	$(YQ2)/client/cl_screen.c \
	$(YQ2)/client/cl_tempentities.c \
	$(YQ2)/client/cl_view.c \
	$(YQ2)/client/menu/menu.c \
	$(YQ2)/client/menu/qmenu.c \
	$(YQ2)/client/menu/videomenu.c \
	$(YQ2)/client/sound/ogg.c \
	$(YQ2)/client/sound/sound.c \
	$(YQ2)/client/sound/wave.c \
	$(YQ2)/client/vid/vid.c

COMMON_SRCS := \
	$(YQ2)/common/argproc.c \
	$(YQ2)/common/clientserver.c \
	$(YQ2)/common/cmdparser.c \
	$(YQ2)/common/collision.c \
	$(YQ2)/common/crc.c \
	$(YQ2)/common/cvar.c \
	$(YQ2)/common/filesystem.c \
	$(YQ2)/common/frame.c \
	$(YQ2)/common/glob.c \
	$(YQ2)/common/md4.c \
	$(YQ2)/common/movemsg.c \
	$(YQ2)/common/netchan.c \
	$(YQ2)/common/pmove.c \
	$(YQ2)/common/szone.c \
	$(YQ2)/common/zone.c \
	$(YQ2)/common/shared/flash.c \
	$(YQ2)/common/shared/rand.c \
	$(YQ2)/common/shared/shared.c \
	$(YQ2)/common/unzip/ioapi.c \
	$(YQ2)/common/unzip/unzip.c \
	$(YQ2)/common/unzip/miniz/miniz.c \
	$(YQ2)/common/unzip/miniz/miniz_tdef.c \
	$(YQ2)/common/unzip/miniz/miniz_tinfl.c

SERVER_SRCS := \
	$(YQ2)/server/sv_cmd.c \
	$(YQ2)/server/sv_conless.c \
	$(YQ2)/server/sv_entities.c \
	$(YQ2)/server/sv_game.c \
	$(YQ2)/server/sv_init.c \
	$(YQ2)/server/sv_main.c \
	$(YQ2)/server/sv_save.c \
	$(YQ2)/server/sv_send.c \
	$(YQ2)/server/sv_user.c \
	$(YQ2)/server/sv_world.c

GAME_SRCS := \
	$(wildcard $(YQ2)/game/*.c) \
	$(wildcard $(YQ2)/game/monster/*/*.c) \
	$(wildcard $(YQ2)/game/player/*.c) \
	$(YQ2)/game/savegame/savegame.c

REF_SOFT_SRCS := \
	$(YQ2)/client/refresh/soft/sw_aclip.c \
	$(YQ2)/client/refresh/soft/sw_alias.c \
	$(YQ2)/client/refresh/soft/sw_bsp.c \
	$(YQ2)/client/refresh/soft/sw_draw.c \
	$(YQ2)/client/refresh/soft/sw_edge.c \
	$(YQ2)/client/refresh/soft/sw_image.c \
	$(YQ2)/client/refresh/soft/sw_light.c \
	$(YQ2)/client/refresh/soft/sw_main.c \
	$(YQ2)/client/refresh/soft/sw_misc.c \
	$(YQ2)/client/refresh/soft/sw_model.c \
	$(YQ2)/client/refresh/soft/sw_part.c \
	$(YQ2)/client/refresh/soft/sw_poly.c \
	$(YQ2)/client/refresh/soft/sw_polyset.c \
	$(YQ2)/client/refresh/soft/sw_rast.c \
	$(YQ2)/client/refresh/soft/sw_scan.c \
	$(YQ2)/client/refresh/soft/sw_sprite.c \
	$(YQ2)/client/refresh/soft/sw_surf.c \
	$(YQ2)/client/refresh/files/common.c \
	$(YQ2)/client/refresh/files/models.c \
	$(YQ2)/client/refresh/files/pcx.c \
	$(YQ2)/client/refresh/files/pvs.c \
	$(YQ2)/client/refresh/files/stb.c \
	$(YQ2)/client/refresh/files/surf.c \
	$(YQ2)/client/refresh/files/wal.c

# gl1 renderer (GLES1 path) — issues textured triangles; we drive the OFQ2 GPU
# directly through openfpga/of_gl1.c (the mini-GL + RI_* window glue), so the
# pristine gl1_sdl.c (SDL_GL) is intentionally NOT compiled. glad.c + qgl.c +
# the shared refresh/files loaders come along.
REF_GL1_SRCS := \
	$(YQ2)/client/refresh/gl1/gl1_aliascache.c \
	$(YQ2)/client/refresh/gl1/gl1_buffer.c \
	$(YQ2)/client/refresh/gl1/gl1_draw.c \
	$(YQ2)/client/refresh/gl1/gl1_image.c \
	$(YQ2)/client/refresh/gl1/gl1_light.c \
	$(YQ2)/client/refresh/gl1/gl1_lightmap.c \
	$(YQ2)/client/refresh/gl1/gl1_main.c \
	$(YQ2)/client/refresh/gl1/gl1_mesh.c \
	$(YQ2)/client/refresh/gl1/gl1_misc.c \
	$(YQ2)/client/refresh/gl1/gl1_model.c \
	$(YQ2)/client/refresh/gl1/gl1_scrap.c \
	$(YQ2)/client/refresh/gl1/gl1_surf.c \
	$(YQ2)/client/refresh/gl1/gl1_warp.c \
	$(YQ2)/client/refresh/gl1/qgl.c \
	$(YQ2)/client/refresh/gl1/glad-gles1/src/glad.c \
	$(YQ2)/client/refresh/files/common.c \
	$(YQ2)/client/refresh/files/models.c \
	$(YQ2)/client/refresh/files/pcx.c \
	$(YQ2)/client/refresh/files/pvs.c \
	$(YQ2)/client/refresh/files/stb.c \
	$(YQ2)/client/refresh/files/surf.c \
	$(YQ2)/client/refresh/files/wal.c

OPENFPGA_SRCS := \
	openfpga/glimp_of.c \
	openfpga/snd_of.c \
	openfpga/hunk_of.c \
	openfpga/input_of.c \
	openfpga/main_of.c \
	openfpga/network_of.c \
	openfpga/of_gl1.c \
	openfpga/of_savefs.c \
	openfpga/of_emit_q2.c \
	openfpga/static_game.c \
	openfpga/system_of.c
# of_emit_q2.c is the single OFQ2 GPU submit layer (owns of_gpu.h's file-static
# ring state). of_gl1.c drives it through the narrow OFQ2_* mirror API for the
# 3D world (Stage 4). It pulls soft/header/local.h for shared types only.

# Renderer: gl1 (REF_GL1_SRCS). The soft renderer (REF_SOFT_SRCS) is retained
# above for reference / quick A-B but is no longer linked.
SRCS = $(OPENFPGA_SRCS) $(CLIENT_SRCS) $(COMMON_SRCS) $(SERVER_SRCS) $(GAME_SRCS) $(REF_GL1_SRCS)
SRCS_CXX =

CFLAGS  = -I. -I$(YQ2)
CFLAGS += -DOFQ2_GPU_PROFILE=$(GPU_PROFILE)
CFLAGS += -I$(YQ2)/common/header -I$(YQ2)/client/header -I$(YQ2)/client/input/header
CFLAGS += -I$(YQ2)/client/sound/header -I$(YQ2)/client/vid/header
CFLAGS += -I$(YQ2)/client/refresh/soft/header -I$(YQ2)/client/refresh/files
CFLAGS += -I$(YQ2)/client/refresh/gl1/header -I$(YQ2)/client/refresh/gl1/glad-gles1/include
CFLAGS += -I$(YQ2)/game/header -I$(YQ2)/server/header
CFLAGS += -std=gnu11 -fcommon -fno-strict-aliasing -fwrapv
CFLAGS += -MMD -MP
CFLAGS += -DYQ2_OPENFPGA=1 -DYQ2OSTYPE=\"openfpgaOS\" -DYQ2ARCH=\"rv32imafc\"
CFLAGS += -DYQ2_GL1_GLES=1
CFLAGS += -DDEFAULT_BASEDIR=\".\" -DNO_SDL_GYRO=1
CFLAGS += -Wno-unused-parameter -Wno-unused-variable -Wno-unused-function
CFLAGS += -Wno-missing-field-initializers -Wno-sign-compare
CFLAGS += -Wno-implicit-function-declaration -Wno-pointer-sign
CFLAGS += -Wno-discarded-qualifiers -Wno-parentheses -Wno-strict-prototypes
CFLAGS += -Wno-old-style-definition -Wno-format -Wno-format-security
CFLAGS += -Wno-implicit-fallthrough -Wno-unused-result -Wno-maybe-uninitialized
CFLAGS += -Wno-address -Wno-enum-conversion -Wno-int-conversion
CFLAGS += -O3 -flto=auto -ffast-math -fno-math-errno -fno-trapping-math
# rv32imafc has no D extension: every double op is a 60-150 cycle
# softfloat call. id-era code is full of unsuffixed FP literals (0.5,
# 1.0/x, 0.99...) that silently promote whole expressions — profiled at
# ~100+ ms/frame across the alias clip/light and world pipelines.
# Single-precision constants restore the effective precision the
# original game had anyway (x87 truncated to float on every store).
CFLAGS += -fsingle-precision-constant
# sdk.mk forces -falign-loops=64, which padded ~113 KB of NOPs (~9% of
# .text) onto loop heads that run once per call, not per pixel. Override
# to 8 (last-wins on the command line, survives LTO). Keep
# -falign-functions=64 (call targets benefit from cache-line alignment).
CFLAGS += -falign-loops=8
# ...and the complementary half: route the double libm *functions*
# (sqrt/sin/floor/...) to their single-precision variants codebase-wide.
# Force-included so it lands ahead of every TU's own <math.h>.
CFLAGS += -include $(YQ2)/common/header/of_mathf.h
LDFLAGS += -O3 -flto=auto

# Declare default target before sdk.mk's rules
.DEFAULT_GOAL := all

include $(SDK_DIR)/sdk.mk

# Also rebuild older objects without .d files on the first build after these
# renderer layout/flag changes. Subsequent header edits use compiler deps.
$(APP_OBJS) $(OF_INIT_OBJ) $(OF_SDL2_OBJ): Makefile
-include $(APP_OBJS:.o=.d) $(OF_INIT_OBJ:.o=.d) $(OF_SDL2_OBJ:.o=.d)

$(CRT_DIR)/start.o: $(CRT_DIR)/start.S
	$(AS) $(ASFLAGS) -c -o $@ $<

# Build ELF, then assemble SD card image in build/<app>/
all: $(OBJ_DIR)/app.elf release
	@$(SIZE) $<

ifeq ($(GPU_PROFILE),1)
# Diagnostic builds never replace the normal Pocket package.
release: $(OBJ_DIR)/app.elf
	@mkdir -p "$(ROOT)/build/diagnostics"
	cp "$<" "$(ROOT)/build/diagnostics/quake2-gpuprofile.elf"
else ifeq ($(TARGET),mister)
release: game-dist        # MiSTer: the per-game bundle IS the release artifact
else
release: $(OBJ_DIR)/app.elf
	@$(SDK_DIR)/platforms/$(TARGET)/image.sh "$(APP)" "$(OBJ_DIR)/app.elf" "$(ROOT)" "$(DIST)" $(GAME_ASSETS)
endif

COMMON = $(shell ls -d $(OUT)/Assets/*/common 2>/dev/null)

debug: all
	@$(ROOT)/scripts/debug.sh "$$(ls -d $(OUT)/Assets/*/common 2>/dev/null)/$(APP).elf"

copy: all
ifeq ($(TARGET),mister)
	@MISTER_IP="$(MISTER_IP)" $(SDK_DIR)/platforms/mister/copy.sh game "$(GAME)" "$(GAME_ELF)" "$(OBJ_DIR)/app.elf"
else
	@MISTER_IP="$(MISTER_IP)" $(SDK_DIR)/platforms/$(TARGET)/copy.sh "$(APP)" "$(OBJ_DIR)/app.elf"
endif

# Engine-only update — identical to `make copy`: for MiSTer that pushes the
# loose games/OpenfpgaOS/<Game>/<GameElf> (the F-loaded engine), leaving
# boot.vhd (your paks) and the saves volume untouched.  Kept as an alias.
copy-app: all
	@$(MAKE) copy

# `make package` for MiSTer builds the per-game locked distribution (a
# boot.vhd shell + .mgl launchers + loose inis + setup.sh + external_files.csv,
# zipped for games/OpenfpgaOS/); every other target keeps the single-image
# path.  scripts/package.sh -> platforms/mister/package.sh auto-detects which.
#
# For MiSTer it ALSO generates the Downloader custom-database delivery artifact
# (releases/mister/quake2.json.zip) via mkdb.py — a real ZIP-wrapped JSON the
# stock MiSTer Downloader syncs.  external_files.csv is folded into it here at
# build time (it is NOT a device file).  db_id must never change once published.
#
# Publishing model (edit these for the real release):
#   MISTER_DB_ID       downloader.ini [section] == db_id
#   MISTER_DB_BASE_URL where the STAGED tree is hosted; a local file at sd path
#                      <p> resolves to  $(MISTER_DB_BASE_URL)<p>  (base_files_url
#                      + path — the Downloader's own rule).  Host the game-dist
#                      tree under games/OpenfpgaOS/ at this base.
#   MISTER_DB_URL      where quake2.json.zip itself is published (for the ini snippet)
MISTER_DB_ID       ?= openfpgaOS/openfpgaos-quake2
MISTER_DB_BASE_URL ?= https://github.com/openfpgaOS/Quake2/releases/latest/download/
MISTER_DB_URL      ?= https://github.com/openfpgaOS/Quake2/releases/latest/download/quake2.json.zip
MISTER_DB          = $(ROOT)/releases/mister/$(APP).json.zip
MISTER_DB_INI      = $(ROOT)/releases/mister/$(APP).downloader.ini
ifeq ($(TARGET),mister)
package: game-dist
	@$(ROOT)/scripts/package.sh $(APP)
	@$(SDK_DIR)/platforms/mister/mkdb.py \
	    --staging "$(GAME_STAGE)" --sd-prefix "games/OpenfpgaOS" \
	    --menu-dir "_Computer/_OpenfpgaOS/_$(GAME)" \
	    --base-url "$(MISTER_DB_BASE_URL)" --url-mode flat --db-id "$(MISTER_DB_ID)" \
	    --external "$(GAME_CSV)" --db-url "$(MISTER_DB_URL)" \
	    --output "$(MISTER_DB)" --ini-out "$(MISTER_DB_INI)"
else
package: all
	@$(ROOT)/scripts/package.sh $(APP)
endif

# ── MiSTer per-game locked distribution (S0 boot shell + S1 saves + .mgl) ─
# The core (openfpgaOS.rbf + os.bin) is game-agnostic.  This game ships a pair
# of images: a READ-ONLY boot.vhd (S0 — /$(GAME)/common: bank.ofsf, paks added
# on-device; NO engine ELF) and a writable $(GAME).saves.vhd (S1 — one
# /$(GAME)/<Instance> subfolder of preallocated config/save slots per instance
# ini).  Splitting them keeps saves on a separate, Downloader-reserved volume
# so an update can replace boot.vhd without touching the player's saves.  The
# engine ELF ships as a LOOSE file ($(GAME)/$(GAME_ELF), next to boot.vhd): the
# firmware F-loads it at ioctl index 2 into ELF staging and serves it as slot 3,
# so an engine update is one loose-file swap (mkdb.py indexes it as a Downloader
# `files` entry) and boot.vhd never changes.  Also ships: a 4-line .mgl launcher
# per instance (S0 boot + S1 saves + F-load ELF index 2 + F-load ini index 1;
# ELF BEFORE ini — the ini F-load resets and triggers the app load), the loose
# per-instance inis, setup.sh (seeds the saves image + injects the user's paks)
# and external_files.csv (freeware asset list).
# `make package TARGET=mister` builds and zips this bundle.
#
# GAME_INIS  : canonical per-instance inis (each with [os] GAME/INSTANCE/ELF)
# GAME_STAGE : games/OpenfpgaOS/-rooted staging that package.sh zips
# GAME_COMMON: throwaway dir feeding mkgame the bank.ofsf (kept OUT of the
#              staging so it is never zipped)
# GAME_BOOT_MB: explicit S0 size.  Doom lets mkgame auto-size (48 MB floor)
#              because its IWADs are ~15 MB; Quake II's retail baseq2 pak set
#              (pak0/pak1/pak2, the three the instance json binds) is ~190 MB,
#              and setup.sh injects those into THIS image on-device — an
#              auto-sized 48 MB shell could not hold them.
GAME        = Quake2
GAME_INIS   = $(DIST)/Assets/quake2/common
GAME_STAGE  = $(ROOT)/build/mister/$(APP)
GAME_COMMON = $(ROOT)/build/mister/.$(APP)-common
GAME_ELF    = quake2.elf
GAME_CSV    = $(DIST)/mister/external_files.csv
GAME_BOOT_MB = 320

game-dist: $(OBJ_DIR)/app.elf
	@rm -rf "$(GAME_STAGE)" "$(GAME_COMMON)"
	@mkdir -p "$(GAME_STAGE)/$(GAME)/wads" "$(GAME_COMMON)"
	@cp "$(RUNTIME)/bank.ofsf" "$(GAME_COMMON)/bank.ofsf"
	@$(SDK_DIR)/platforms/mister/mkgame.sh --no-elf --saves-out "$(GAME_STAGE)/$(GAME)/$(GAME).saves.vhd" "$(GAME)" "$(GAME_INIS)" "$(GAME_COMMON)" "$(GAME_STAGE)/$(GAME)/boot.vhd" $(GAME_BOOT_MB)
	@$(SDK_DIR)/platforms/mister/mkmgl.sh "$(GAME)" "$(GAME_INIS)" "$(GAME_STAGE)"
	@cp "$(OBJ_DIR)/app.elf" "$(GAME_STAGE)/$(GAME)/$(GAME_ELF)"
	@cp "$(GAME_INIS)"/*.ini "$(GAME_STAGE)/$(GAME)/"
	@sed 's|^GAME_DEFAULT=.*|GAME_DEFAULT="$(GAME)"|' "$(SDK_DIR)/platforms/mister/setup.sh" > "$(GAME_STAGE)/$(GAME)/setup.sh"
	@chmod +x "$(GAME_STAGE)/$(GAME)/setup.sh"
	@cp "$(GAME_CSV)" "$(GAME_STAGE)/$(GAME)/external_files.csv"
	@rm -rf "$(GAME_COMMON)"
	@echo "  game-dist staged -> $(GAME_STAGE)"

test: app_pc

check-renderer:
	python3 $(ROOT)/tools/check_quake2_renderer.py

clean: sdk-clean
ifeq ($(GPU_PROFILE),1)
	rm -f "$(ROOT)/build/diagnostics/quake2-gpuprofile.elf"
else
	rm -rf $(OUT)
endif

.PHONY: all release debug copy copy-app package test check-renderer clean game-dist
