/*
 * of_malloc.c — dlmalloc configured for openfpgaOS
 *
 * This wraps Doug Lea's malloc (MIT-0 license) with our platform config.
 * Compiled as part of each app via the CRT.
 */

#include "dlmalloc_config.h"
#include "dlmalloc.c"
