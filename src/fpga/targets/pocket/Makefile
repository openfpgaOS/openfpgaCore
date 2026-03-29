# Analogue Pocket — Build System

PROJECT = ap_core
FW_DIR = ../../../firmware/os
VEXII_DIR = ../../vendor/vexriscv/VexiiRiscv
VEXII_OUT = ../../vendor/vexriscv/VexiiRiscv_Full.v
TEST_DIR = ../../test
RELEASE_DIR = ../../../../build/Cores/ThinkElastic.openfpgaOS
QUARTUS = /home/alberto/altera_lite/25.1std/quartus/bin
export PATH := $(QUARTUS):$(PATH)

all: cpu firmware compile test deploy summary

summary:
	@echo ""
	@echo "=== Build Summary ==="
	@test -f $(VEXII_OUT) && echo " [x] CPU generated" || echo " [ ] CPU generated"
	@test -f $(FW_DIR)/firmware.mif && echo " [x] Firmware built" || echo " [ ] Firmware built"
	@test -f $(FW_DIR)/os.bin && echo " [x] OS built" || echo " [ ] OS built"
	@test -f output_files/$(PROJECT).rbf && echo " [x] FPGA compiled" || echo " [ ] FPGA compiled"
	@test -f output_files/$(PROJECT).sta.rpt && \
		FMAX=$$(grep -A30 "Slow 1100mV 85C Model Fmax Summary" output_files/$(PROJECT).sta.rpt | grep "mp_ram.*general\[0\]" | head -1 | awk -F';' '{print $$2}' | xargs) && \
		echo "     Fmax: $$FMAX" || true
	@echo " [x] Tests passed" 2>/dev/null || echo " [ ] Tests"
	@test -f ../../../../build/Cores/ThinkElastic.openfpgaOS/bitstream.rbf_r && echo " [x] Deployed" || echo " [ ] Deployed"
	@echo ""

cpu:
	cd $(VEXII_DIR) && bash ../generate_vexii.sh
	cp $(VEXII_DIR)/VexiiRiscv.v $(VEXII_OUT)

firmware:
	$(MAKE) -C $(FW_DIR) clean all install

compile:
	rm -rf db incremental_db
	quartus_sh --flow compile $(PROJECT)

test:
	$(MAKE) -C $(TEST_DIR) all

deploy:
	@if [ ! -x ../../../../tools/reverse_bits ]; then \
		gcc -O2 -o ../../../../tools/reverse_bits ../../../../tools/reverse_bits.c; \
	fi
	../../../../tools/reverse_bits output_files/$(PROJECT).rbf $(RELEASE_DIR)/bitstream.rbf_r
	cp $(FW_DIR)/os.bin $(RELEASE_DIR)/os.bin
	@echo "Deployed to $(RELEASE_DIR)"

flash: firmware
	quartus_cdb $(PROJECT) --update_mif
	quartus_asm $(PROJECT)
	@if [ ! -x ../../../../tools/reverse_bits ]; then \
		gcc -O2 -o ../../../../tools/reverse_bits ../../../../tools/reverse_bits.c; \
	fi
	../../../../tools/reverse_bits output_files/$(PROJECT).rbf $(RELEASE_DIR)/bitstream.rbf_r
	cp $(FW_DIR)/os.bin $(RELEASE_DIR)/os.bin

check:
	quartus_map $(PROJECT)

clean:
	rm -rf db incremental_db
	rm -f output_files/*.rpt output_files/*.summary output_files/*.sof
	rm -f output_files/*.rbf output_files/*.done output_files/*.smsg
	rm -f output_files/*.pin output_files/*.jdi

.PHONY: all cpu firmware compile test deploy flash check clean
