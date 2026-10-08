ENTRY(_start)
MEMORY { code (rx) : ORIGIN = 0, LENGTH = 64K
         data (rw) : ORIGIN = 0x10000, LENGTH = 64K }
SECTIONS {
    .text : { KEEP(*(.text.start)) *(.text*) *(.rodata*) } > code
    .data : { *(.sdata*) *(.data*) } > data
    .bss (NOLOAD) : { *(.sbss*) *(.bss*) *(COMMON) } > data
}
