/*
 * openfpgaOS Analogizer HAL Implementation
 *
 * Boot starts from the Pocket interact.json values, mirrored into CPU
 * sysregs by the FPGA. After filesystem_init() registers filenames, the
 * kernel may load analogizer.cfg and override those defaults before the
 * app starts. The file format is intentionally simple text so users can
 * edit it directly on the SD card.
 */

#include "analogizer.h"
#include "file.h"
#include "save.h"
#include "snac.h"
#include "syscall.h"
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define ANALOGCFG_MAGIC       0x47464341u  /* 'ACFG' legacy binary */
#define ANALOGCFG_VERSION     1u
#define ANALOGCFG_MAX_READ    1024u
#define ANALOGCFG_CRC_LEN     offsetof(analogcfg_bin_t, crc32)

typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint16_t version;
    uint16_t flags;
    uint8_t  enabled;
    uint8_t  video_mode;
    uint8_t  snac_type;
    uint8_t  snac_assignment;
    int8_t   h_offset;
    int8_t   v_offset;
    uint8_t  reserved[2];
    uint32_t crc32;
    uint8_t  pad[12];
} analogcfg_bin_t;

typedef struct {
    const char *name;
    int value;
} named_value_t;

static of_analogizer_state_t anlg_state;

static int clamp_int(int v, int lo, int hi) {
    return v < lo ? lo : (v > hi ? hi : v);
}

static int ascii_lower(int c) {
    return (c >= 'A' && c <= 'Z') ? c + 32 : c;
}

static int str_eq_ci(const char *a, const char *b) {
    while (*a && *b) {
        if (ascii_lower((unsigned char)*a) !=
            ascii_lower((unsigned char)*b))
            return 0;
        a++;
        b++;
    }
    return *a == 0 && *b == 0;
}

static int is_space(int c) {
    return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}

static char *trim(char *s) {
    while (is_space((unsigned char)*s))
        s++;

    char *end = s;
    while (*end)
        end++;

    while (end > s && is_space((unsigned char)end[-1]))
        *--end = 0;

    return s;
}

static int parse_int_value(const char *s, int *out) {
    int sign = 1;
    int base = 10;
    int value = 0;
    int any = 0;

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
        int digit;
        if (*s >= '0' && *s <= '9')
            digit = *s - '0';
        else if (*s >= 'a' && *s <= 'f')
            digit = *s - 'a' + 10;
        else if (*s >= 'A' && *s <= 'F')
            digit = *s - 'A' + 10;
        else
            return 0;

        if (digit >= base)
            return 0;

        value = value * base + digit;
        any = 1;
        s++;
    }

    if (!any)
        return 0;

    *out = value * sign;
    return 1;
}

static int parse_bool_value(const char *s, int *out) {
    if (str_eq_ci(s, "true") || str_eq_ci(s, "yes") ||
        str_eq_ci(s, "on") || str_eq_ci(s, "enabled")) {
        *out = 1;
        return 1;
    }

    if (str_eq_ci(s, "false") || str_eq_ci(s, "no") ||
        str_eq_ci(s, "off") || str_eq_ci(s, "disabled")) {
        *out = 0;
        return 1;
    }

    int value;
    if (!parse_int_value(s, &value))
        return 0;

    *out = value != 0;
    return 1;
}

static int parse_named_or_int(const char *s, const named_value_t *names,
                              int count, int *out) {
    for (int i = 0; i < count; i++) {
        if (str_eq_ci(s, names[i].name)) {
            *out = names[i].value;
            return 1;
        }
    }

    return parse_int_value(s, out);
}

static int snac_type_supported(uint8_t type) {
    switch (type) {
    case SNAC_NONE:
    case SNAC_DB15:
    case SNAC_NES:
    case SNAC_SNES:
    case SNAC_PCE_2BTN:
    case SNAC_PCE_6BTN:
    case SNAC_PCE_MULTITAP:
    case SNAC_DB15_FAST:
    case SNAC_SNES_SWAP:
    case SNAC_PSX:
    case SNAC_PSX_FAST:
    case SNAC_PSX_ANALOG:
    case SNAC_PSX_ANALOG_FAST:
        return 1;
    default:
        return 0;
    }
}

static uint8_t legacy_binary_video_mode(uint8_t mode) {
    static const uint8_t map[] = {
        ANLG_VIDEO_RGBS,     /* RGB */
        ANLG_VIDEO_YC_NTSC,  /* NTSC */
        ANLG_VIDEO_YC_PAL,   /* PAL */
        ANLG_VIDEO_YC_NTSC,  /* YC-NTSC */
        ANLG_VIDEO_YC_PAL,   /* YC-PAL */
        ANLG_VIDEO_SC_0PCT,  /* SCART */
    };

    if (mode < (uint8_t)(sizeof(map) / sizeof(map[0])))
        return map[mode];

    return mode;
}

static uint8_t legacy_binary_snac_type(uint8_t type) {
    static const uint8_t map[] = {
        SNAC_NONE,
        SNAC_DB15,
        SNAC_NES,
        SNAC_SNES,
        SNAC_PCE_2BTN,
        SNAC_PCE_6BTN,
        SNAC_PCE_MULTITAP,
        SNAC_PSX,
        SNAC_PSX_FAST,
        SNAC_PSX_ANALOG,
        SNAC_PSX_ANALOG_FAST,
    };

    if (type < (uint8_t)(sizeof(map) / sizeof(map[0])))
        return map[type];

    return type;
}

static uint32_t crc32_compute(const void *data, uint32_t len) {
    const uint8_t *p = (const uint8_t *)data;
    uint32_t crc = 0xFFFFFFFFu;

    for (uint32_t i = 0; i < len; i++) {
        crc ^= p[i];
        for (int b = 0; b < 8; b++)
            crc = (crc >> 1) ^ (0xEDB88320u & -(crc & 1u));
    }

    return ~crc;
}

static void analogizer_clamp_state(of_analogizer_state_t *s) {
    s->enabled = s->enabled ? 1 : 0;
    s->video_mode = (uint8_t)clamp_int(s->video_mode, 0, 15);
    s->snac_type = (uint8_t)clamp_int(s->snac_type, 0, 31);
    if (!snac_type_supported(s->snac_type))
        s->snac_type = SNAC_NONE;
    s->snac_assignment = (uint8_t)clamp_int(s->snac_assignment, 0, 15);
    s->h_offset = (int8_t)clamp_int(s->h_offset, -32, 31);
    s->v_offset = (int8_t)clamp_int(s->v_offset, -16, 15);
}

static uint32_t analogizer_pack_settings(const of_analogizer_state_t *s) {
    return ((uint32_t)s->snac_type & 0x1Fu) |
           (((uint32_t)s->snac_assignment & 0x0Fu) << 6) |
           (((uint32_t)s->video_mode & 0x0Fu) << 10) |
           (s->enabled ? (1u << 15) : 0u);
}

static void analogizer_init_snac(void) {
    if (anlg_state.snac_type != SNAC_NONE)
        snac_init(anlg_state.snac_type);
    else
        snac_init(SNAC_NONE);
}

static void analogizer_apply_state(const of_analogizer_state_t *state) {
    anlg_state = *state;
    analogizer_clamp_state(&anlg_state);

    ANALOGIZER_H_OFFSET = (uint32_t)(int32_t)anlg_state.h_offset;
    ANALOGIZER_V_OFFSET = (uint32_t)(int32_t)anlg_state.v_offset;
    ANALOGIZER_SETTINGS = analogizer_pack_settings(&anlg_state);

    analogizer_init_snac();
}

static void analogizer_read_hardware_defaults(void) {
    uint32_t settings = ANALOGIZER_SETTINGS;

    anlg_state.snac_type = settings & 0x1Fu;
    anlg_state.snac_assignment = (settings >> 6) & 0x0Fu;
    anlg_state.video_mode = (settings >> 10) & 0x0Fu;
    anlg_state.enabled = (settings >> 15) & 1u;
    anlg_state.h_offset = (int8_t)ANALOGIZER_H_OFFSET;
    anlg_state.v_offset = (int8_t)ANALOGIZER_V_OFFSET;
    analogizer_clamp_state(&anlg_state);
}

static int parse_binary_config(const uint8_t *buf, uint32_t len,
                               of_analogizer_state_t *out) {
    if (len < sizeof(analogcfg_bin_t))
        return -1;

    const analogcfg_bin_t *cfg = (const analogcfg_bin_t *)buf;
    if (cfg->magic != ANALOGCFG_MAGIC || cfg->version != ANALOGCFG_VERSION)
        return -1;

    if (crc32_compute(cfg, ANALOGCFG_CRC_LEN) != cfg->crc32)
        return -1;

    out->enabled = cfg->enabled;
    /* The original analogcfg binary writer stored UI list indices, not
     * hardware enum values. Normalize those files while keeping text
     * configs on the native enum names and integers. */
    out->video_mode = legacy_binary_video_mode(cfg->video_mode);
    out->snac_type = legacy_binary_snac_type(cfg->snac_type);
    out->snac_assignment = cfg->snac_assignment;
    out->h_offset = cfg->h_offset;
    out->v_offset = cfg->v_offset;
    analogizer_clamp_state(out);
    return 0;
}

static int parse_text_config(char *buf, of_analogizer_state_t *out) {
    static const named_value_t video_names[] = {
        {"rgbs", ANLG_VIDEO_RGBS},
        {"rgb", ANLG_VIDEO_RGBS},
        {"rgb_s", ANLG_VIDEO_RGBS},
        {"rgsb", ANLG_VIDEO_RGSB},
        {"rgb_sog", ANLG_VIDEO_RGSB},
        {"ypbpr", ANLG_VIDEO_YPBPR},
        {"component", ANLG_VIDEO_YPBPR},
        {"yc_ntsc", ANLG_VIDEO_YC_NTSC},
        {"yc-ntsc", ANLG_VIDEO_YC_NTSC},
        {"svideo", ANLG_VIDEO_YC_NTSC},
        {"s_video", ANLG_VIDEO_YC_NTSC},
        {"s-video", ANLG_VIDEO_YC_NTSC},
        {"composite", ANLG_VIDEO_YC_NTSC},
        {"cvbs", ANLG_VIDEO_YC_NTSC},
        {"ntsc", ANLG_VIDEO_YC_NTSC},
        {"yc_pal", ANLG_VIDEO_YC_PAL},
        {"yc-pal", ANLG_VIDEO_YC_PAL},
        {"svideo_pal", ANLG_VIDEO_YC_PAL},
        {"s_video_pal", ANLG_VIDEO_YC_PAL},
        {"s-video-pal", ANLG_VIDEO_YC_PAL},
        {"composite_pal", ANLG_VIDEO_YC_PAL},
        {"cvbs_pal", ANLG_VIDEO_YC_PAL},
        {"pal", ANLG_VIDEO_YC_PAL},
        {"scart", ANLG_VIDEO_SC_0PCT},
        {"rgb_scart", ANLG_VIDEO_SC_0PCT},
        {"sc_0pct", ANLG_VIDEO_SC_0PCT},
        {"sc_50pct", ANLG_VIDEO_SC_50PCT},
        {"sc_hq2x", ANLG_VIDEO_SC_HQ2X},
        {"rgbs_off", ANLG_VIDEO_RGBS | ANLG_VIDEO_POCKET_OFF},
        {"rgsb_off", ANLG_VIDEO_RGSB | ANLG_VIDEO_POCKET_OFF},
        {"ypbpr_off", ANLG_VIDEO_YPBPR | ANLG_VIDEO_POCKET_OFF},
        {"component_off", ANLG_VIDEO_YPBPR | ANLG_VIDEO_POCKET_OFF},
        {"yc_ntsc_off", ANLG_VIDEO_YC_NTSC | ANLG_VIDEO_POCKET_OFF},
        {"svideo_off", ANLG_VIDEO_YC_NTSC | ANLG_VIDEO_POCKET_OFF},
        {"composite_off", ANLG_VIDEO_YC_NTSC | ANLG_VIDEO_POCKET_OFF},
        {"cvbs_off", ANLG_VIDEO_YC_NTSC | ANLG_VIDEO_POCKET_OFF},
        {"yc_pal_off", ANLG_VIDEO_YC_PAL | ANLG_VIDEO_POCKET_OFF},
        {"svideo_pal_off", ANLG_VIDEO_YC_PAL | ANLG_VIDEO_POCKET_OFF},
        {"composite_pal_off", ANLG_VIDEO_YC_PAL | ANLG_VIDEO_POCKET_OFF},
        {"cvbs_pal_off", ANLG_VIDEO_YC_PAL | ANLG_VIDEO_POCKET_OFF},
        {"scart_off", ANLG_VIDEO_SC_0PCT | ANLG_VIDEO_POCKET_OFF},
        {"rgb_scart_off", ANLG_VIDEO_SC_0PCT | ANLG_VIDEO_POCKET_OFF},
        {"sc_0pct_off", ANLG_VIDEO_SC_0PCT | ANLG_VIDEO_POCKET_OFF},
        {"sc_50pct_off", ANLG_VIDEO_SC_50PCT | ANLG_VIDEO_POCKET_OFF},
        {"sc_hq2x_off", ANLG_VIDEO_SC_HQ2X | ANLG_VIDEO_POCKET_OFF},
    };
    static const named_value_t snac_names[] = {
        {"none", SNAC_NONE},
        {"db15", SNAC_DB15},
        {"nes", SNAC_NES},
        {"snes", SNAC_SNES},
        {"pce_2btn", SNAC_PCE_2BTN},
        {"pce-2btn", SNAC_PCE_2BTN},
        {"pce_6btn", SNAC_PCE_6BTN},
        {"pce-6btn", SNAC_PCE_6BTN},
        {"pce_multitap", SNAC_PCE_MULTITAP},
        {"pce-multitap", SNAC_PCE_MULTITAP},
        {"pce_mt", SNAC_PCE_MULTITAP},
        {"db15_fast", SNAC_DB15_FAST},
        {"snes_swap", SNAC_SNES_SWAP},
        {"psx", SNAC_PSX},
        {"psx_fast", SNAC_PSX_FAST},
        {"psx_analog", SNAC_PSX_ANALOG},
        {"psx_analog_fast", SNAC_PSX_ANALOG_FAST},
    };
    static const named_value_t assign_names[] = {
        {"snac_p1", 0},
        {"snac_to_p1", 0},
        {"snac_p2", 1},
        {"snac_to_p2", 1},
        {"dual", 2},
        {"snac_dual", 2},
        {"swap", 3},
        {"snac_swap", 3},
    };

    int seen = 0;
    char *p = buf;

    while (*p) {
        char *line = p;
        while (*p && *p != '\n' && *p != '\r')
            p++;
        if (*p) {
            *p++ = 0;
            while (*p == '\n' || *p == '\r')
                p++;
        }

        for (char *c = line; *c; c++) {
            if (*c == '#' || *c == ';') {
                *c = 0;
                break;
            }
        }

        char *key = trim(line);
        if (!*key || *key == '[')
            continue;

        char *eq = key;
        while (*eq && *eq != '=')
            eq++;
        if (*eq != '=')
            continue;

        *eq = 0;
        char *value = trim(eq + 1);
        key = trim(key);
        if (!*key || !*value)
            continue;

        int parsed;
        if (str_eq_ci(key, "enabled") || str_eq_ci(key, "output")) {
            if (parse_bool_value(value, &parsed)) {
                out->enabled = parsed ? 1 : 0;
                seen++;
            }
        } else if (str_eq_ci(key, "video") ||
                   str_eq_ci(key, "video_mode") ||
                   str_eq_ci(key, "mode") ||
                   str_eq_ci(key, "output_mode") ||
                   str_eq_ci(key, "video_output")) {
            if (parse_named_or_int(value, video_names,
                                   (int)(sizeof(video_names) / sizeof(video_names[0])),
                                   &parsed)) {
                out->video_mode = (uint8_t)parsed;
                seen++;
            }
        } else if (str_eq_ci(key, "snac") ||
                   str_eq_ci(key, "snac_type") ||
                   str_eq_ci(key, "snac_mode") ||
                   str_eq_ci(key, "controller") ||
                   str_eq_ci(key, "controller_type")) {
            if (parse_named_or_int(value, snac_names,
                                   (int)(sizeof(snac_names) / sizeof(snac_names[0])),
                                   &parsed)) {
                out->snac_type = (uint8_t)parsed;
                seen++;
            }
        } else if (str_eq_ci(key, "assignment") ||
                   str_eq_ci(key, "snac_assignment") ||
                   str_eq_ci(key, "snac_assign")) {
            if (parse_named_or_int(value, assign_names,
                                   (int)(sizeof(assign_names) / sizeof(assign_names[0])),
                                   &parsed)) {
                out->snac_assignment = (uint8_t)parsed;
                seen++;
            }
        } else if (str_eq_ci(key, "h_offset") ||
                   str_eq_ci(key, "hoffset") ||
                   str_eq_ci(key, "h")) {
            if (parse_int_value(value, &parsed)) {
                out->h_offset = (int8_t)parsed;
                seen++;
            }
        } else if (str_eq_ci(key, "v_offset") ||
                   str_eq_ci(key, "voffset") ||
                   str_eq_ci(key, "v")) {
            if (parse_int_value(value, &parsed)) {
                out->v_offset = (int8_t)parsed;
                seen++;
            }
        }
    }

    if (!seen)
        return -1;

    analogizer_clamp_state(out);
    return 0;
}

static int read_config_file(const char *filename, uint8_t *buf,
                            uint32_t cap, uint32_t *len_out) {
    uint32_t slot_id;
    if (file_slot_find(filename, &slot_id) < 0)
        return -1;

    long size = of_file_size(slot_id);
    if (size <= 0)
        return -1;

    uint32_t len = (uint32_t)size;
    if (len >= cap)
        len = cap - 1;

    int rc;
    if (of_nvslot_is_supported(slot_id))
        rc = of_nvslot_read(slot_id, buf, 0, len);
    else
        rc = of_file_read(slot_id, 0, buf, len);

    if (rc <= 0)
        return -1;

    *len_out = (uint32_t)rc;
    buf[rc] = 0;
    return 0;
}

void of_analogizer_init(void) {
    analogizer_read_hardware_defaults();
    analogizer_init_snac();
}

int of_analogizer_load_config(const char *filename) {
    uint8_t buf[ANALOGCFG_MAX_READ];
    uint32_t len;

    if (!filename || read_config_file(filename, buf, sizeof(buf), &len) < 0)
        return -1;

    of_analogizer_state_t loaded = anlg_state;
    int rc;
    if (len >= sizeof(uint32_t) &&
        buf[0] == 'A' && buf[1] == 'C' && buf[2] == 'F' && buf[3] == 'G') {
        rc = parse_binary_config(buf, len, &loaded);
    } else {
        rc = parse_text_config((char *)buf, &loaded);
    }

    if (rc < 0)
        return rc;

    analogizer_apply_state(&loaded);
    return 0;
}

const of_analogizer_state_t *of_analogizer_get_state(void) {
    return &anlg_state;
}

int of_analogizer_is_enabled(void) {
    return anlg_state.enabled;
}

int of_analogizer_get_video_mode(void) {
    return anlg_state.video_mode;
}
