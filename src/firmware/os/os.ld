/*
 * openfpgaOS Linker Script — v2 memory arch
 *
 * Memory layout:
 * - BRAM:     0x00000000 (32KB)   - Bootloader + trap handler + hot code
 * - OSDATA:   0x10320000 (768KB)  - OS code + data + rodata + BSS (ALL in SDRAM)
 * - Apps:     0x10400000+         - Dynamically loaded application (SDRAM)
 *
 * v2 rationale: CRAM1 retired entirely.  CRAM0 is bridge staging only
 * (uncached + non-exec per PMA), so the OS kernel can no longer run
 * from CRAM0.  Everything that used to live in CRAM0 (just OS .text)
 * moves into the OSDATA region alongside .rodata/.data/.bss.
 *
 * Boot flow: bridge DMAs os.bin into CRAM0 slot 1 scratch, then the
 * BRAM bootloader flips CRAM0_MODE to CPU and memcpy's the whole blob
 * into SDRAM at __osdata_vma start (== __os_entry).  Single
 * contiguous copy — no CRAM0 destination anymore.
 */

ENTRY(_start)

MEMORY {
    BRAM      (rwx) : ORIGIN = 0x00000000, LENGTH = 32K
    OSDATA    (rwx) : ORIGIN = 0x10320000, LENGTH = 768K
}

/* Layout symbols */
__sdram_base = 0x10000000;
__sdram_size = 64M;
__os_entry = 0x10320000;
__app_load_base = 0x10400000;

SECTIONS {
    /* Bootloader code - in BRAM, runs at startup */
    .boot : {
        KEEP(*(.text.start))
        *(.text.boot*)
        /* Boot-critical object files — match any target (pocket, sim, ...) */
        targets/*/terminal.o(.text*)
        targets/*/terminal.o(.rodata*)
        targets/*/terminal.o(.srodata*)
        targets/*/boot/boot.o(.text*)
        targets/*/boot/boot.o(.rodata*)
        targets/*/boot/boot.o(.srodata*)
        *(.rodata.boot*)
        . = ALIGN(4);
    } > BRAM

    .boot_data : {
        *(.data.boot*)
        *(.sdata.boot*)
        . = ALIGN(4);
    } > BRAM

    .boot_bss : {
        . = ALIGN(4);
        __bss_start = .;
        *(.bss.boot*)
        *(.sbss.boot*)
        targets/*/boot/boot.o(.bss*)
        targets/*/boot/boot.o(.sbss*)
        . = ALIGN(4);
        __bss_end = .;
    } > BRAM

    /* Hot code/data pinned in BRAM */
    .fasttext : {
        . = ALIGN(4);
        __fasttext_start = .;
        KEEP(*(.fasttext*))
        KEEP(*(.fastrodata*))
        . = ALIGN(4);
        __fasttext_end = .;
    } > BRAM

    .fastdata : {
        . = ALIGN(4);
        __fastdata_start = .;
        KEEP(*(.fastdata*))
        . = ALIGN(4);
        __fastdata_end = .;
    } > BRAM

    /* === SDRAM: OS code + read-only + initialised data ===
     * v2 arch: .text moves here from CRAM0 since CRAM0 is now
     * non-executable (PMA exe=0).  The whole OS image (text + rodata
     * + data) is packed into one contiguous section so boot.c can
     * do a single memcpy from the CRAM0 scratch blob into SDRAM. */
    .osdata __os_entry : {
        . = ALIGN(16);
        __osdata_lma        = LOADADDR(.osdata);
        __osdata_vma        = .;
        __text_start        = .;
        KEEP(*(.text*))
        KEEP(*(.app_fasttext*))
        KEEP(*(.app_fastrodata*))
        . = ALIGN(16);
        __text_end          = .;
        __osdata_rodata_vma = .;
        KEEP(*(.rodata*))
        KEEP(*(.srodata*))
        . = ALIGN(16);
        __osdata_rodata_vma_end = .;
        __osdata_data_vma       = .;
        *(.data*)
        *(.sdata*)
        . = ALIGN(4);
        __osdata_data_vma_end = .;
        __osdata_vma_end      = .;
    } > OSDATA

    /* os.bin = .osdata (which includes .text, .rodata, .data).
     * Bootloader reads the whole blob from CRAM0 slot 1 scratch and
     * memcpy's it to SDRAM at __osdata_vma == __os_entry.  No split. */
    __os_load_addr          = __os_entry;
    __os_text_size          = __text_end - __text_start;
    __os_copy_size          = __osdata_vma_end - __osdata_vma;
    __osdata_init_vma_start = __osdata_vma;
    __osdata_init_size      = __osdata_vma_end - __osdata_vma;

    /* === SDRAM: OS BSS (zeroed at boot) === */
    .os_bss : {
        . = ALIGN(4);
        __os_bss_start = .;
        *(.bss*)
        *(.sbss*)
        *(COMMON)
        . = ALIGN(4);
        __os_bss_end = .;
    } > OSDATA

    /* Memory regions.
     *
     * __runtime_stack_top is 16 MB below the SDRAM PMA end (0x14000000) —
     * a conservative known-good SDRAM region.  With the stack top at the
     * PMA edge, speculative loads past the top (branch prediction paths,
     * cache line ahead-fill) cross into unmapped memory and surface as
     * architectural load
     * access faults.  16 MB of headroom is more than any speculation
     * window reaches. */
    __os_heap_start = __os_bss_end;
    __save_region = 0x13C00000;
    __runtime_stack_top = 0x13000000;
    __runtime_stack_size = 512K;
    __runtime_stack_bottom = __runtime_stack_top - __runtime_stack_size;

    /* Stack at end of BRAM */
    __stack_top = ORIGIN(BRAM) + LENGTH(BRAM);

    /* App BRAM region — must match APP_BRAM_BASE/END in hal/regs.h.
     * OS region extended from 0x2000 to 0x4000 (16 KB total) to hold
     * swmixer + smp_voice hot state in .fastdata: the timer ISR writes
     * both every tick, and ISR SDRAM writes race with GPU/bridge bus
     * traffic (see start.S::_trap_entry comment).  Pinning in BRAM
     * breaks the race. */
    __app_bram_start = 0x00004000;
    __app_bram_end   = 0x00007800;  /* caps at 0x7800, libc at 0x7C00, trap stack at 0x7E00 */

    /* Provide symbols for C code */
    PROVIDE(_start_addr = ORIGIN(BRAM));
    PROVIDE(_stack_top = __stack_top);
    PROVIDE(_bss_start = __bss_start);
    PROVIDE(_bss_end = __bss_end);
    PROVIDE(_os_bss_start = __os_bss_start);
    PROVIDE(_os_bss_end = __os_bss_end);
    PROVIDE(_os_load_addr = __os_load_addr);
    PROVIDE(_os_text_size = __os_text_size);
    PROVIDE(_os_copy_size = __os_copy_size);
    PROVIDE(_osdata_init_vma_start = __osdata_init_vma_start);
    PROVIDE(_osdata_init_size = __osdata_init_size);
    PROVIDE(_runtime_stack_top = __runtime_stack_top);
    PROVIDE(__app_load_base = __app_load_base);
    PROVIDE(_app_bram_start = __app_bram_start);
    PROVIDE(_app_bram_end = __app_bram_end);

    ASSERT(__fastdata_end <= __app_bram_start, "OS BRAM overflow: OS sections exceed 8KB reserved region")
    ASSERT(__fastdata_end <= (__stack_top - 8192), "BRAM overflow: insufficient stack headroom")
    ASSERT(__os_bss_end <= (ORIGIN(OSDATA) + LENGTH(OSDATA)),
           "OS text+rodata+data+bss too large: exceeds OSDATA region in SDRAM")
}
