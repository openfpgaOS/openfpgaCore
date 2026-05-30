/*
 * openfpgaOS boot/app configuration.
 *
 * os.ini is loaded once by the kernel from a fixed data slot, parsed in
 * place, and exposed to apps through the services table.
 */

#ifndef OFOS_KERNEL_CONFIG_H
#define OFOS_KERNEL_CONFIG_H

#include <stdint.h>

#define OS_CONFIG_SLOT_ID 2u

#define OF_CONFIG_ERR_NOENT (-2)
#define OF_CONFIG_ERR_INVAL (-22)
#define OF_CONFIG_ERR_NOSPC (-28)
#define OF_CONFIG_ERR_RANGE (-34)
#define OF_CONFIG_ERR_E2BIG (-7)

int of_config_load(uint32_t slot_id);
int of_config_get(const char *section, const char *key,
                  char *out, uint32_t out_len);
int of_config_get_int(const char *section, const char *key,
                      int default_value);
int of_config_get_bool(const char *section, const char *key,
                       int default_value);
int of_config_next(const char *section, uint32_t *cursor,
                   char *key_out, uint32_t key_len,
                   char *value_out, uint32_t value_len);

int of_config_loaded(void);
int of_config_error_line(void);

#endif /* OFOS_KERNEL_CONFIG_H */
