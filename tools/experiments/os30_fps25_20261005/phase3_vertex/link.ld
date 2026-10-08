ENTRY(_start)
PHDRS { harness PT_LOAD FLAGS(7); corpus PT_LOAD FLAGS(4); }
SECTIONS {
 . = 0x10320000;
 .text : { *(.text.start) *(.text*) } :harness
 .rodata : { *(.rodata*) } :harness
 .data : { *(.data*) *(.sdata*) } :harness
 . = ALIGN(16);
 __global_pointer$ = . + 0x800;
 __bss_start = .;
 .bss : { *(.bss*) *(.sbss*) *(COMMON) } :harness
 . = ALIGN(16);
 __bss_end = .;
 ASSERT(__bss_end < 0x103e0000, "Harness overlaps original app/stack")
 . = 0x11400000;
 .corpus : { *(.corpus) } :corpus
}
