/*
 * openfpgaOS INI configuration parser.
 *
 * Supported syntax:
 *   [section]
 *   KEY=VALUE
 *
 * Blank lines and lines starting with '#' or ';' are ignored. Section and key
 * lookup is ASCII case-insensitive. Values preserve case and internal spaces.
 */

#include "config.h"
#include "../hal/file.h"
#include "../os_string.h"

#define CONFIG_MAX_FILE     (16u * 1024u)
#define CONFIG_MAX_ENTRIES  256u

typedef struct {
    char *section;
    char *key;
    char *value;
} config_entry_t;

static char config_buf[CONFIG_MAX_FILE + 1u];
static config_entry_t config_entries[CONFIG_MAX_ENTRIES];
static uint32_t config_entry_count;
static int config_parse_error_line;

static int cfg_isspace(char c) {
    return c == ' ' || c == '\t' || c == '\r' || c == '\n' ||
           c == '\v' || c == '\f';
}

static char cfg_tolower(char c) {
    if (c >= 'A' && c <= 'Z')
        return (char)(c + ('a' - 'A'));
    return c;
}

static int cfg_stricmp(const char *a, const char *b) {
    while (*a && *b) {
        char ca = cfg_tolower(*a++);
        char cb = cfg_tolower(*b++);
        if (ca != cb)
            return (int)(unsigned char)ca - (int)(unsigned char)cb;
    }
    return (int)(unsigned char)cfg_tolower(*a) -
           (int)(unsigned char)cfg_tolower(*b);
}

static char *cfg_trim(char *s) {
    while (*s && cfg_isspace(*s))
        s++;

    char *end = s + strlen(s);
    while (end > s && cfg_isspace(end[-1]))
        *--end = '\0';

    return s;
}

static int cfg_copy(char *out, uint32_t out_len, const char *value) {
    if (!out || out_len == 0)
        return OF_CONFIG_ERR_INVAL;

    uint32_t len = (uint32_t)strlen(value);
    if (len + 1u > out_len)
        return OF_CONFIG_ERR_NOSPC;

    memcpy(out, value, len + 1u);
    return 0;
}

static config_entry_t *cfg_find(const char *section, const char *key) {
    if (!section || !key)
        return 0;

    for (int i = (int)config_entry_count - 1; i >= 0; i--) {
        config_entry_t *e = &config_entries[i];
        if (cfg_stricmp(e->section, section) == 0 &&
            cfg_stricmp(e->key, key) == 0)
            return e;
    }

    return 0;
}

static int cfg_parse_int(const char *s, int *out) {
    int sign = 1;
    uint32_t base = 10;
    uint32_t value = 0;
    int digits = 0;

    while (cfg_isspace(*s))
        s++;

    if (*s == '-') {
        sign = -1;
        s++;
    } else if (*s == '+') {
        s++;
    }

    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {
        base = 16;
        s += 2;
    }

    while (*s) {
        uint32_t d;
        if (*s >= '0' && *s <= '9')
            d = (uint32_t)(*s - '0');
        else if (*s >= 'a' && *s <= 'f')
            d = 10u + (uint32_t)(*s - 'a');
        else if (*s >= 'A' && *s <= 'F')
            d = 10u + (uint32_t)(*s - 'A');
        else
            break;

        if (d >= base)
            break;

        value = value * base + d;
        digits++;
        s++;
    }

    while (cfg_isspace(*s))
        s++;
    if (*s || !digits)
        return -1;

    *out = (sign < 0) ? -(int)value : (int)value;
    return 0;
}

static int cfg_parse_buffer(uint32_t size) {
    config_entry_count = 0;
    config_parse_error_line = 0;

    char *current_section = "";
    char *p = config_buf;
    uint32_t line_no = 1;

    while (p < config_buf + size) {
        char *line = p;
        while (p < config_buf + size && *p != '\n' && *p != '\r')
            p++;
        if (p < config_buf + size) {
            *p++ = '\0';
            while (p < config_buf + size && (*p == '\n' || *p == '\r'))
                p++;
        }

        char *s = cfg_trim(line);
        if (!*s || *s == '#' || *s == ';') {
            line_no++;
            continue;
        }

        if (*s == '[') {
            char *close = s + 1;
            while (*close && *close != ']')
                close++;
            if (*close != ']') {
                config_parse_error_line = (int)line_no;
                return OF_CONFIG_ERR_INVAL;
            }
            *close = '\0';
            current_section = cfg_trim(s + 1);
            if (!*current_section) {
                config_parse_error_line = (int)line_no;
                return OF_CONFIG_ERR_INVAL;
            }
            line_no++;
            continue;
        }

        char *eq = s;
        while (*eq && *eq != '=')
            eq++;
        if (*eq != '=') {
            config_parse_error_line = (int)line_no;
            return OF_CONFIG_ERR_INVAL;
        }

        *eq = '\0';
        char *key = cfg_trim(s);
        char *value = cfg_trim(eq + 1);
        if (!*key || config_entry_count >= CONFIG_MAX_ENTRIES) {
            config_parse_error_line = (int)line_no;
            return !*key ? OF_CONFIG_ERR_INVAL : OF_CONFIG_ERR_NOSPC;
        }

        config_entries[config_entry_count].section = current_section;
        config_entries[config_entry_count].key = key;
        config_entries[config_entry_count].value = value;
        config_entry_count++;
        line_no++;
    }

    return 0;
}

int of_config_load(uint32_t slot_id) {
    config_entry_count = 0;
    config_parse_error_line = 0;
    config_buf[0] = '\0';

    int64_t size = of_file_size64(slot_id);
    if (size <= 0) {
        return 0;
    }

    if ((uint64_t)size > CONFIG_MAX_FILE)
        return OF_CONFIG_ERR_E2BIG;

    int rc = of_file_read_chunked(slot_id, 0, config_buf, (uint32_t)size);
    if (rc < 0)
        return rc;

    config_buf[(uint32_t)size] = '\0';
    rc = cfg_parse_buffer((uint32_t)size);
    if (rc < 0)
        return rc;

    return 0;
}

int of_config_get(const char *section, const char *key,
                  char *out, uint32_t out_len) {
    config_entry_t *e = cfg_find(section, key);
    if (!e)
        return OF_CONFIG_ERR_NOENT;
    return cfg_copy(out, out_len, e->value);
}

int of_config_get_int(const char *section, const char *key,
                      int default_value) {
    config_entry_t *e = cfg_find(section, key);
    int value;
    if (!e || cfg_parse_int(e->value, &value) < 0)
        return default_value;
    return value;
}

int of_config_get_bool(const char *section, const char *key,
                       int default_value) {
    config_entry_t *e = cfg_find(section, key);
    if (!e)
        return default_value;

    if (cfg_stricmp(e->value, "1") == 0 ||
        cfg_stricmp(e->value, "true") == 0 ||
        cfg_stricmp(e->value, "yes") == 0 ||
        cfg_stricmp(e->value, "on") == 0)
        return 1;

    if (cfg_stricmp(e->value, "0") == 0 ||
        cfg_stricmp(e->value, "false") == 0 ||
        cfg_stricmp(e->value, "no") == 0 ||
        cfg_stricmp(e->value, "off") == 0)
        return 0;

    return default_value;
}

int of_config_next(const char *section, uint32_t *cursor,
                   char *key_out, uint32_t key_len,
                   char *value_out, uint32_t value_len) {
    if (!section || !cursor)
        return OF_CONFIG_ERR_INVAL;

    uint32_t i = *cursor;
    while (i < config_entry_count) {
        config_entry_t *e = &config_entries[i++];
        if (cfg_stricmp(e->section, section) != 0)
            continue;

        int rc = cfg_copy(key_out, key_len, e->key);
        if (rc < 0)
            return rc;
        rc = cfg_copy(value_out, value_len, e->value);
        if (rc < 0)
            return rc;

        *cursor = i;
        return 0;
    }

    return OF_CONFIG_ERR_NOENT;
}

int of_config_error_line(void) {
    return config_parse_error_line;
}
