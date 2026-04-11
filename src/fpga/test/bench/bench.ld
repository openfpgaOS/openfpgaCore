/* Linker script for Verilator system benchmark */
/* BRAM region is 32KB per VexiiRiscv PMA (generate_vexii.sh:
 * --region base=0,size=8000,main=1,exe=1).  Anything above 0x8000
 * faults as a store access exception.  Keep everything packed here. */
MEMORY {
    BRAM (rwx) : ORIGIN = 0x00000000, LENGTH = 32K
}

SECTIONS {
    .text : { *(.text.init) *(.text*) } > BRAM
    .rodata : { *(.rodata*) } > BRAM
    .data : { *(.data*) } > BRAM
    .bss : { *(.bss*) *(COMMON) } > BRAM
    _stack_top = ORIGIN(BRAM) + LENGTH(BRAM) - 0x10;
}
