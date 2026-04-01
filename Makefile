# openfpgaOS Makefile
#
# Build for a specific FPGA target. Default: pocket (Analogue Pocket)
#
#   make                Build firmware + apps for TARGET
#   make fpga           Compile FPGA bitstream for TARGET
#   make full           Full build (FPGA + firmware + package)
#   make TARGET=mister  Build for MiSTer (when available)

# Target selection
TARGET ?= pocket

# Configuration
CORE_NAME = ThinkElastic.openfpgaOS
QUARTUS_PROJECT = ap_core

# Directories
FPGA_DIR = src/fpga
FPGA_TARGET_DIR = $(FPGA_DIR)/targets/$(TARGET)
FIRMWARE_DIR = src/firmware
OS_DIR = src/firmware/os
CHIP32_DIR = src/chip32/$(TARGET)
OUTPUT_DIR = build
RELEASE_CORE_DIR = $(OUTPUT_DIR)/Cores/$(CORE_NAME)
RELEASE_PLATFORMS_DIR = $(OUTPUT_DIR)/Platforms
RELEASE_ASSETS_DIR = $(OUTPUT_DIR)/Assets/openfpgaos/common
RELEASE_INSTANCE_DIR = $(OUTPUT_DIR)/Assets/openfpgaos/$(CORE_NAME)

# Files
BITSTREAM_SOURCE = $(FPGA_TARGET_DIR)/output_files/$(QUARTUS_PROJECT).rbf
BITSTREAM_TARGET = $(RELEASE_CORE_DIR)/bitstream.rbf_r
FIRMWARE_SOURCE = $(OS_DIR)/os.bin
FIRMWARE_TARGET = $(RELEASE_ASSETS_DIR)/os.bin
# JSON configuration files (in dist/core/)
DIST_DIR = dist/core
JSON_FILES = core.json video.json audio.json input.json data.json variants.json interact.json

# Tools
REVERSE_BITS = tools/reverse_bits

# Default target - package without recompiling FPGA
all: package

# Full build - compile FPGA, firmware, and package
full: fpga firmware package

# Firmware source files for dependency tracking
FW_SOURCES = $(wildcard $(OS_DIR)/hal/*.c $(OS_DIR)/targets/$(TARGET)/*.c $(OS_DIR)/targets/$(TARGET)/boot/*.c $(OS_DIR)/targets/$(TARGET)/boot/*.S $(OS_DIR)/kernel/*.c $(OS_DIR)/*.ld)
FW_HEADERS = $(wildcard $(OS_DIR)/hal/*.h $(OS_DIR)/targets/$(TARGET)/*.h $(OS_DIR)/targets/$(TARGET)/boot/*.h $(OS_DIR)/kernel/*.h)
FW_MIF = $(FPGA_TARGET_DIR)/firmware.mif

# VexiiRiscv CPU generation (requires java + sbt)
VEXII_DIR = $(FPGA_DIR)/vendor/vexriscv
VEXII_SCRIPT = $(VEXII_DIR)/generate_vexii.sh
VEXII_OUTPUT = $(VEXII_DIR)/VexiiRiscv_Full.v

$(VEXII_OUTPUT): $(VEXII_SCRIPT)
	@echo "Generating VexiiRiscv CPU..."
	cd $(VEXII_DIR) && bash generate_vexii.sh
	@echo "VexiiRiscv generation complete"

cpu: $(VEXII_OUTPUT)

# Compile FPGA with Quartus (builds firmware + CPU first)
# Clears Quartus cache to ensure MIF changes are picked up
fpga: $(FW_MIF) $(VEXII_OUTPUT) clean-fpga-cache
	@echo "Compiling FPGA design..."
	@if ! command -v quartus_sh >/dev/null 2>&1; then \
		echo "Error: quartus_sh not found in PATH"; \
		exit 1; \
	fi
	cd $(FPGA_TARGET_DIR) && quartus_sh --flow compile $(QUARTUS_PROJECT)
	@echo "FPGA compilation complete"

# Build firmware and install MIF to FPGA core directory (only when sources change)
$(FW_MIF): $(FW_SOURCES) $(FW_HEADERS)
	@echo "Building OS firmware and generating MIF..."
	$(MAKE) -C $(OS_DIR) install
	@echo "Firmware MIF ready for FPGA build"

# Force rebuild of firmware MIF (for manual use)
firmware-mif:
	@echo "Building OS firmware and generating MIF..."
	$(MAKE) -C $(OS_DIR) install
	@echo "Firmware MIF ready for FPGA build"

# Build firmware
firmware:
	@echo "Building OS firmware..."
	$(MAKE) -C $(OS_DIR)
	@echo "OS firmware build complete"

# Build Chip32 loader
chip32:
	$(MAKE) -C $(CHIP32_DIR)

# Package release (uses existing bitstream)
package: $(REVERSE_BITS) check-bitstream release-dirs copy-bitstream copy-chip32 copy-firmware copy-json copy-platform copy-icon install-txt
	@echo ""
	@echo "Build complete!"
	@echo "Release package: $(OUTPUT_DIR)/"
	@echo ""
	@tree -L 4 $(OUTPUT_DIR) 2>/dev/null || find $(OUTPUT_DIR) -type f | sort

# Check that bitstream exists
check-bitstream:
	@if [ ! -f "$(BITSTREAM_SOURCE)" ]; then \
		echo "Error: Bitstream not found at $(BITSTREAM_SOURCE)"; \
		echo "Run 'make fpga' first or compile with Quartus"; \
		exit 1; \
	fi

# Create release directory structure
release-dirs:
	@echo "Creating release directories..."
	@rm -rf $(OUTPUT_DIR)
	@mkdir -p $(RELEASE_CORE_DIR)
	@mkdir -p $(RELEASE_PLATFORMS_DIR)/_images
	@mkdir -p $(RELEASE_ASSETS_DIR)


# Build bit reversal tool
$(REVERSE_BITS): tools/reverse_bits.c
	@echo "Compiling bit reversal tool..."
	gcc -O2 -o $@ $<

# Convert and copy bitstream
copy-bitstream: $(REVERSE_BITS)
	@echo "Converting bitstream to RBF_R format..."
	$(REVERSE_BITS) $(BITSTREAM_SOURCE) $(BITSTREAM_TARGET)

# Copy Chip32 loader
copy-chip32:
	@echo "Copying Chip32 loader..."
	cp $(CHIP32_DIR)/loader.bin $(RELEASE_CORE_DIR)/loader.bin

# Copy firmware (os.bin)
copy-firmware:
	@echo "Copying os.bin..."
	cp $(FIRMWARE_SOURCE) $(FIRMWARE_TARGET)

# Copy JSON configuration files
copy-json:
	@echo "Copying configuration files..."
	@for f in $(JSON_FILES); do \
		cp $(DIST_DIR)/$$f $(RELEASE_CORE_DIR)/; \
	done

# Copy platform definition and images
copy-platform:
	@echo "Copying platform files..."
	@cp dist/platforms/*.json $(RELEASE_PLATFORMS_DIR)/
	@cp dist/platforms/_images/*.bin $(RELEASE_PLATFORMS_DIR)/_images/

# Copy core icon if it exists
copy-icon:
	@if [ -f "dist/core/icon.bin" ]; then \
		echo "Copying core icon..."; \
		cp dist/core/icon.bin $(RELEASE_CORE_DIR)/; \
	fi

# Generate installation instructions
define INSTALL_TEXT
Analogue Pocket Core Installation Instructions
================================================

Core: $(CORE_NAME)

Installation Steps:
-------------------

1. Insert your Analogue Pocket's SD card into your computer

2. Copy the entire contents of this release folder to your SD card root
   - Merge with existing folders if they exist

3. Safely eject the SD card and insert it back into your Analogue Pocket

4. Power on your Analogue Pocket

5. Navigate to the "Cores" menu and select "$(CORE_NAME)"

Directory Structure:
--------------------
Your SD card should have this structure:

SD Card Root/
+-- Assets/
|   +-- openfpgaos/
|       +-- common/
|           +-- app.bin
+-- Cores/
|   +-- $(CORE_NAME)/
|       +-- bitstream.rbf_r
|       +-- core.json
|       +-- video.json
|       +-- audio.json
|       +-- input.json
|       +-- data.json
|       +-- variants.json
|       +-- interact.json
|       +-- icon.bin
+-- Platforms/
    +-- _images/
    |   +-- openfpgaos.bin
    +-- openfpgaos.json

Troubleshooting:
----------------
- Make sure the SD card is formatted as exFAT
- Ensure your Analogue Pocket firmware is version 1.1 or later
- Check that all files copied correctly without errors

For more information, visit:
https://www.analogue.co/developer
endef
export INSTALL_TEXT

install-txt:
	@echo "Generating installation instructions..."
	@echo "$$INSTALL_TEXT" > $(OUTPUT_DIR)/INSTALL.txt

# Clean all build artifacts
clean:
	@echo "Cleaning..."
	rm -rf $(OUTPUT_DIR)
	rm -f $(REVERSE_BITS)
	rm -f $(VEXII_OUTPUT) $(VEXII_OUTPUT).bak
	$(MAKE) -C $(OS_DIR) clean
	$(MAKE) -C $(CHIP32_DIR) clean

# Fast firmware update - only updates MIF in existing bitstream (no full recompile)
# Use this when you only changed firmware and want a quick rebuild (~1 min vs ~15 min)
firmware-update: $(FW_MIF)
	@echo "Updating MIF in existing bitstream..."
	@if [ ! -f "$(FPGA_TARGET_DIR)/output_files/$(QUARTUS_PROJECT).sof" ]; then \
		echo "Error: No existing compile found. Run 'make fpga' first."; \
		exit 1; \
	fi
	cd $(FPGA_TARGET_DIR) && quartus_cdb --update_mif $(QUARTUS_PROJECT)
	cd $(FPGA_TARGET_DIR) && quartus_asm $(QUARTUS_PROJECT)
	@echo "Firmware updated in bitstream"

# Alias for firmware-update
fw: firmware-update package

# Clean Quartus cache (forces MIF files to be re-read)
clean-fpga-cache:
	@echo "Clearing Quartus cache to pick up MIF changes..."
	rm -rf $(FPGA_TARGET_DIR)/db $(FPGA_TARGET_DIR)/incremental_db

# Clean FPGA build artifacts
clean-fpga: clean-fpga-cache
	@echo "Cleaning FPGA build artifacts..."
	rm -f $(FPGA_TARGET_DIR)/output_files/*

# Quick target (alias for package)
quick: package

# Program FPGA via JTAG (for development)
program: $(FW_MIF)
	@echo "Programming FPGA via JTAG..."
	$(MAKE) -C $(FPGA_TARGET_DIR) program

.PHONY: all full fpga cpu firmware-mif firmware chip32 firmware-update fw package check-bitstream release-dirs copy-bitstream copy-chip32 copy-firmware copy-json copy-platform copy-icon install-txt clean clean-fpga-cache clean-fpga quick program
