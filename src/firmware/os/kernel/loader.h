/*
 * openfpgaOS ELF Loader
 * Loads static-PIE ELF32 binaries from data slots into SDRAM
 */

#ifndef OFOS_LOADER_H
#define OFOS_LOADER_H

#include <stdint.h>

/* Result of loading an ELF binary */
typedef struct {
    uintptr_t entry;        /* Entry point address */
    uintptr_t load_base;    /* Base address where ELF was loaded */
    uintptr_t bss_end;      /* End of BSS (start of app heap) */
    uintptr_t stack_top;    /* Top of app stack */
} elf_load_result_t;

/* Load an ELF binary from a data slot.
 * slot_id: APF data slot containing the ELF
 * load_addr: SDRAM address to load at (must be page-aligned)
 * result: filled with load results on success
 * Returns 0 on success, negative on error. */
int elf_load(uint32_t slot_id, uintptr_t load_addr,
             elf_load_result_t *result);

/* Jump to loaded application.
 * Sets up argc/argv on the new stack and jumps to entry point.
 * Does not return. */
void elf_exec(const elf_load_result_t *result,
              int argc, char **argv) __attribute__((noreturn));

#endif /* OFOS_LOADER_H */
