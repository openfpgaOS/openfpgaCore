/* Linker script for Verilator system benchmark */
MEMORY {
    BRAM (rwx) : ORIGIN = 0x00000000, LENGTH = 256K
}

SECTIONS {
    .text : { *(.text.init) *(.text*) } > BRAM
    .rodata : { *(.rodata*) } > BRAM
    .data : { *(.data*) } > BRAM
    .bss : { *(.bss*) *(COMMON) } > BRAM
    _stack_top = ORIGIN(BRAM) + LENGTH(BRAM) - 0x1000;
}
