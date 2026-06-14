#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------

# menu.elf — openfpgaOS game launcher (Architecture A, MiSTer).
#
# Builds against a generated SDK (the dual-mirror SDK tree produced by the OS
# source tree's `make sdk DEST=...`).  Point SDK_DIR at that tree's src/sdk:
#
#   make SDK_DIR=/path/to/sdk/src/sdk
#
# Output is app.elf — deploy it as the MiSTer image-root /app.elf so the root
# os.ini boots the launcher.

SDK_DIR ?= ../../sdk
SRCS    := main.c
BUILD_DIR := .

include $(SDK_DIR)/sdk.mk

.DEFAULT_GOAL := $(BUILD_DIR)/app.elf

.PHONY: menu
menu: $(BUILD_DIR)/app.elf
	@cp -f $(BUILD_DIR)/app.elf menu.elf
	@echo "menu.elf ready (deploy as image-root /app.elf)"
