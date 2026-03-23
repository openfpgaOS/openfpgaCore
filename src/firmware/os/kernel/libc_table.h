/*
 * openfpgaOS libc Jump Table
 * Populates a function pointer table at a fixed address for apps to use.
 */

#ifndef OFOS_LIBC_TABLE_H
#define OFOS_LIBC_TABLE_H

/* Initialize the libc jump table at OF_LIBC_ADDR.
 * Must be called before launching the application. */
void libc_table_init(void);

#endif /* OFOS_LIBC_TABLE_H */
