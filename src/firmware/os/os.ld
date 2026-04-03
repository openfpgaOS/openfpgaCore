/*
 * openfpgaOS Linker Script
 *
 * Memory layout:
 * - BRAM:     0x00000000 (32KB) - Bootloader + trap handler + hot code
 * - CRAM0:    0x30000000 (1MB)  - OS kernel code + data + BSS
 * - App area: 0x30100000+       - Dynamically loaded application (CRAM0)
 * - Terminal: 0x20000000 (8KB)
 * - SysRegs:  0x40000000
 */

ENTRY(_start)

MEMORY {
    BRAM      (rwx) : ORIGIN = 0x00000000, LENGTH = 32K
    CRAM0     (rwx) : ORIGIN = 0x30000000, LENGTH = 1M
}

/* Layout symbols */
__sdram_base = 0x10000000;
__sdram_size = 64M;
__os_entry = 0x30000000;
__app_load_base = 0x30100000;

SECTIONS {
    /* Bootloader code - in BRAM, runs at startup */
    .boot : {
        KEEP(*(.text.start))
        *(.text.boot*)
        /* Boot-critical object files */
        targets/pocket/terminal.o(.text*)
        targets/pocket/terminal.o(.rodata*)
        targets/pocket/terminal.o(.srodata*)
        targets/pocket/boot/boot.o(.text*)
        targets/pocket/boot/boot.o(.rodata*)
        targets/pocket/boot/boot.o(.srodata*)
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
        targets/pocket/boot/boot.o(.bss*)
        targets/pocket/boot/boot.o(.sbss*)
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

    /* === SDRAM: OS kernel code === */

    .text __os_entry : {
        __text_start = .;
        KEEP(*(.text*))
        KEEP(*(.rodata*))
        KEEP(*(.srodata*))
        . = ALIGN(4);
        __text_end = .;
    } > CRAM0

    .data : {
        __data_start = .;
        KEEP(*(.data*))
        KEEP(*(.sdata*))
        . = ALIGN(4);
        __data_end = .;
    } > CRAM0

    /* os.bin = .text + .data */
    __os_load_addr = __os_entry;
    __os_copy_size = __data_end - __text_start;

    /* OS BSS */
    .os_bss : {
        . = ALIGN(4);
        __os_bss_start = .;
        *(.bss*)
        *(.sbss*)
        *(COMMON)
        . = ALIGN(4);
        __os_bss_end = .;
    } > CRAM0

    /* Memory regions */
    __os_heap_start = __os_bss_end;
    __save_region = 0x13C00000;
    __runtime_stack_top = 0x14000000;
    __runtime_stack_size = 512K;
    __runtime_stack_bottom = __runtime_stack_top - __runtime_stack_size;

    /* Stack at end of BRAM */
    __stack_top = ORIGIN(BRAM) + LENGTH(BRAM);

    /* App BRAM region — must match APP_BRAM_BASE/END in hal/regs.h
     * and OF_APP_BRAM_BASE/END in of_bram.h (SDK). */
    __app_bram_start = 0x00002000;
    __app_bram_end   = ORIGIN(BRAM) + LENGTH(BRAM) - 512;

    /* Provide symbols for C code */
    PROVIDE(_start_addr = ORIGIN(BRAM));
    PROVIDE(_stack_top = __stack_top);
    PROVIDE(_bss_start = __bss_start);
    PROVIDE(_bss_end = __bss_end);
    PROVIDE(_os_bss_start = __os_bss_start);
    PROVIDE(_os_bss_end = __os_bss_end);
    PROVIDE(_os_load_addr = __os_load_addr);
    PROVIDE(_os_copy_size = __os_copy_size);
    PROVIDE(_runtime_stack_top = __runtime_stack_top);
    PROVIDE(__app_load_base = __app_load_base);
    PROVIDE(_app_bram_start = __app_bram_start);
    PROVIDE(_app_bram_end = __app_bram_end);

    ASSERT(__fastdata_end <= __app_bram_start, "OS BRAM overflow: OS sections exceed 8KB reserved region")
    ASSERT(__fastdata_end <= (__stack_top - 8192), "BRAM overflow: insufficient stack headroom")
    ASSERT(__os_bss_end <= 0x300FF000, "OS too large: exceeds 1MB CRAM0 OS region")
}
