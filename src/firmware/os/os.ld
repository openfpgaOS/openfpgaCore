/*
 * openfpgaOS Linker Script
 *
 * Memory layout:
 * - BRAM:     0x00000000 (32KB)   - Bootloader + trap handler + hot code
 * - CRAM0:    0x30000000 (1MB)    - OS kernel code (.text only)
 * - OSDATA:   0x10320000 (768KB)  - OS data + rodata + BSS in SDRAM
 * - Apps:     0x10400000+         - Dynamically loaded application (SDRAM)
 *
 * Split-memory rationale (2026-04-17): CRAM0 is accessed only by i_axi
 * (instruction fetches for .text) so it sees single-master traffic. OS
 * .data / .rodata / .bss live in SDRAM so d-cache (mem_axi) never
 * touches CRAM0 during runtime. This eliminates the i_axi+mem_axi
 * multi-port interleaving on CRAM0 that was suspected of causing
 * cache-line corruption in sync-burst mode.
 *
 * LMA for .osdata_rodata / .osdata_data stays in CRAM0 (AT> CRAM0) so
 * os.bin remains a single contiguous blob. The bootloader splits the
 * blob at __os_text_size: bytes [0, __os_text_size) go to CRAM0
 * (OS .text) and bytes [__os_text_size, __os_copy_size) go directly
 * to SDRAM at __osdata_init_vma_start (the OS .rodata + .data init
 * image).  No post-load CRAM0→SDRAM copy.
 */

ENTRY(_start)

MEMORY {
    BRAM      (rwx) : ORIGIN = 0x00000000, LENGTH = 32K
    CRAM0     (rx)  : ORIGIN = 0x30000000, LENGTH = 1M
    OSDATA    (rw)  : ORIGIN = 0x10320000, LENGTH = 768K
}

/* Layout symbols */
__sdram_base = 0x10000000;
__sdram_size = 64M;
__os_entry = 0x30000000;
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

    /* === CRAM0: OS kernel code (i_axi only) === */

    .text __os_entry : {
        __text_start = .;
        KEEP(*(.text*))
        /* Sweep up orphan .app_fasttext emitted by OS sources that
         * decorate functions with OF_FASTTEXT (see hal/link.c). On
         * this target BRAM is consumed by the bootloader, so these
         * end up as regular i_axi code in CRAM0. */
        KEEP(*(.app_fasttext*))
        KEEP(*(.app_fastrodata*))
        . = ALIGN(4);
        __text_end = .;
    } > CRAM0

    /* === SDRAM: OS read-only + initialised data ===
     * Merged into a single section (.osdata) to keep VMA and LMA
     * boundaries bit-for-bit identical.  If we split .rodata and
     * .data into separate output sections, ld may add alignment
     * padding to the VMA side (e.g. 8-byte alignment inherited from
     * a .data member) without matching padding in the LMA packing,
     * which shifts every field in .data by the padding amount.
     * The boot loader streams a contiguous blob from CRAM0 LMA to
     * SDRAM VMA, so any VMA/LMA divergence is a memory-content bug.
     *
     * Alignment inside the merged section uses ALIGN(16) to satisfy
     * the strongest object alignment without introducing boundary
     * ambiguity between the rodata and data subranges. */
    .osdata : {
        . = ALIGN(16);
        __osdata_lma        = LOADADDR(.osdata);
        __osdata_vma        = .;
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
    } > OSDATA AT> CRAM0

    /* os.bin = .text + .osdata packed contiguously. The bootloader
     * splits the blob by offset:
     *   bytes [0, __os_text_size)           → CRAM0 0x30000000 (uncached alias)
     *   bytes [__os_text_size, __os_copy_size) → SDRAM __osdata_init_vma_start
     * Because .osdata is ONE section, VMA end - VMA start exactly
     * equals LMA end - LMA start — no alignment padding mismatch. */
    __os_load_addr          = __os_entry;
    __os_text_size          = __text_end - __text_start;
    __os_copy_size          = LOADADDR(.osdata) + SIZEOF(.osdata) - __text_start;
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

    /* Memory regions */
    __os_heap_start = __os_bss_end;
    __save_region = 0x13C00000;
    __runtime_stack_top = 0x14000000;
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
    ASSERT((LOADADDR(.osdata) + SIZEOF(.osdata)) <= 0x300FF000,
           "OS code+rodata+data too large: exceeds 1MB CRAM0 region")
    ASSERT(__os_bss_end <= (ORIGIN(OSDATA) + LENGTH(OSDATA)),
           "OS .bss too large: exceeds OSDATA region in SDRAM")
}
