/*
 * sf2_to_ofsf.c -- Convert SF2 soundfont to openfpgaOS .ofsf runtime bank.
 *
 * Standalone host tool.  Build with:
 *   gcc -O2 -Wall -Wextra -std=c99 -o sf2_to_ofsf sf2_to_ofsf.c -lm
 *
 * Usage:
 *   sf2_to_ofsf input.sf2 output.ofsf [--max-size=NM] [--sample-rate=RATE]
 *
 * Reads an SF2 (SoundFont 2.04) RIFF file and writes a flat .ofsf binary
 * matching the layout in of_smp_bank.h:
 *   [header 32B] [preset_index 1024B] [zones N*80B] [sample blob]
 */

#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <stdarg.h>
#include <math.h>
#include <errno.h>

#ifdef _WIN32
  /* No mmap on Windows -- use malloc+fread fallback */
  #define USE_MMAP 0
#else
  #include <sys/mman.h>
  #include <sys/stat.h>
  #include <fcntl.h>
  #include <unistd.h>
  #define USE_MMAP 1
#endif

/* ------------------------------------------------------------------ */
/* OFSF output format (must match of_smp_bank.h exactly)              */
/* ------------------------------------------------------------------ */

#define OFSF_MAGIC      0x4F465346
#define OFSF_VERSION    1
#define OFSF_PRESET_COUNT 256

#define OFSF_LOOP_NONE    0
#define OFSF_LOOP_FORWARD 1
#define OFSF_LOOP_BIDI    3

typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint32_t version;
    uint32_t sample_rate;
    uint32_t zone_count;
    uint32_t sample_data_offset;
    uint32_t sample_data_size;
    uint32_t flags;
    uint32_t reserved;
} ofsf_header_t;

typedef struct __attribute__((packed)) {
    uint16_t zone_start;
    uint16_t zone_count;
} ofsf_preset_t;

typedef struct __attribute__((packed)) {
    uint8_t  key_lo;
    uint8_t  key_hi;
    uint8_t  vel_lo;
    uint8_t  vel_hi;

    uint32_t sample_offset;
    uint32_t sample_length;
    uint32_t loop_start;
    uint32_t loop_end;

    uint8_t  loop_mode;
    uint8_t  root_key;
    int8_t   fine_tune;
    int8_t   coarse_tune;

    int16_t  vol_delay;
    int16_t  vol_attack;
    int16_t  vol_hold;
    int16_t  vol_decay;
    int16_t  vol_sustain;
    int16_t  vol_release;

    int16_t  mod_delay;
    int16_t  mod_attack;
    int16_t  mod_hold;
    int16_t  mod_decay;
    int16_t  mod_sustain;
    int16_t  mod_release;
    int16_t  mod_env_to_pitch;
    int16_t  mod_env_to_filter;

    int16_t  mod_lfo_delay;
    int16_t  mod_lfo_freq;
    int16_t  mod_lfo_to_pitch;
    int16_t  mod_lfo_to_filter;
    int16_t  mod_lfo_to_volume;

    int16_t  vib_lfo_delay;
    int16_t  vib_lfo_freq;
    int16_t  vib_lfo_to_pitch;

    int16_t  initial_fc;
    int16_t  initial_q;

    int16_t  initial_attn;
    int16_t  pan;

    uint8_t  exclusive_class;
    uint8_t  _pad[3];
} ofsf_zone_t;

/* Compile-time check: zone must be exactly 80 bytes */
_Static_assert(sizeof(ofsf_zone_t) == 80, "ofsf_zone_t must be 80 bytes");
_Static_assert(sizeof(ofsf_header_t) == 32, "ofsf_header_t must be 32 bytes");
_Static_assert(sizeof(ofsf_preset_t) == 4, "ofsf_preset_t must be 4 bytes");

/* ------------------------------------------------------------------ */
/* SF2 structures                                                     */
/* ------------------------------------------------------------------ */

/* Generator IDs */
enum {
    GEN_startAddrsOffset        = 0,
    GEN_endAddrsOffset          = 1,
    GEN_startloopAddrsOffset    = 2,
    GEN_endloopAddrsOffset      = 3,
    GEN_startAddrsCoarseOffset  = 4,
    GEN_modLfoToPitch           = 5,
    GEN_vibLfoToPitch           = 6,
    GEN_modEnvToPitch           = 7,
    GEN_initialFilterFc         = 8,
    GEN_initialFilterQ          = 9,
    GEN_modLfoToFilterFc        = 10,
    GEN_modEnvToFilterFc        = 11,
    GEN_endAddrsCoarseOffset    = 12,
    GEN_modLfoToVolume          = 13,
    GEN_pan                     = 17,
    GEN_delayModLFO             = 21,
    GEN_freqModLFO              = 22,
    GEN_delayVibLFO             = 23,
    GEN_freqVibLFO              = 24,
    GEN_delayModEnv             = 25,
    GEN_attackModEnv            = 26,
    GEN_holdModEnv              = 27,
    GEN_decayModEnv             = 28,
    GEN_sustainModEnv           = 29,
    GEN_releaseModEnv           = 30,
    GEN_delayVolEnv             = 33,
    GEN_attackVolEnv            = 34,
    GEN_holdVolEnv              = 35,
    GEN_decayVolEnv             = 36,
    GEN_sustainVolEnv           = 37,
    GEN_releaseVolEnv           = 38,
    GEN_keyRange                = 43,
    GEN_velRange                = 44,
    GEN_startloopAddrsCoarseOffset = 45,
    GEN_initialAttenuation      = 48,
    GEN_endloopAddrsCoarseOffset = 50,
    GEN_coarseTune              = 51,
    GEN_fineTune                = 52,
    GEN_sampleID                = 53,
    GEN_sampleModes             = 54,
    GEN_scaleTuning             = 56,
    GEN_exclusiveClass          = 57,
    GEN_instrument              = 41,
    GEN_overridingRootKey       = 58,
    GEN_COUNT                   = 60    /* max generator ID + 1 that we track */
};

/* SF2 sub-chunk records -- packed on-disk layout */

#pragma pack(push, 1)

typedef struct {
    char     name[20];
    uint16_t preset;
    uint16_t bank;
    uint16_t bag_ndx;
    uint32_t library;
    uint32_t genre;
    uint32_t morphology;
} sf2_phdr_t;

typedef struct {
    uint16_t gen_ndx;
    uint16_t mod_ndx;
} sf2_bag_t;

typedef struct {
    uint16_t oper;
    uint16_t amount;    /* genAmountType stored as uint16_t */
} sf2_gen_t;

typedef struct {
    char     name[20];
    uint16_t bag_ndx;
} sf2_inst_t;

typedef struct {
    char     name[20];
    uint32_t start;
    uint32_t end;
    uint32_t loop_start;
    uint32_t loop_end;
    uint32_t sample_rate;
    uint8_t  original_key;
    int8_t   correction;
    uint16_t sample_link;
    uint16_t sample_type;
} sf2_shdr_t;

/* SF2 modulator record (we skip these but need the size) */
typedef struct {
    uint16_t src_oper;
    uint16_t dest_oper;
    int16_t  amount;
    uint16_t amt_src_oper;
    uint16_t trans_oper;
} sf2_mod_t;

#pragma pack(pop)

_Static_assert(sizeof(sf2_phdr_t) == 38, "sf2_phdr_t must be 38 bytes");
_Static_assert(sizeof(sf2_bag_t) == 4, "sf2_bag_t must be 4 bytes");
_Static_assert(sizeof(sf2_gen_t) == 4, "sf2_gen_t must be 4 bytes");
_Static_assert(sizeof(sf2_inst_t) == 22, "sf2_inst_t must be 22 bytes");
_Static_assert(sizeof(sf2_shdr_t) == 46, "sf2_shdr_t must be 46 bytes");
_Static_assert(sizeof(sf2_mod_t) == 10, "sf2_mod_t must be 10 bytes");

/* ------------------------------------------------------------------ */
/* Parsed SF2 state                                                   */
/* ------------------------------------------------------------------ */

typedef struct {
    /* Raw file data */
    uint8_t *data;
    size_t   size;
    int      use_mmap;

    /* smpl chunk (16-bit signed PCM) */
    const int16_t *smpl;
    uint32_t       smpl_samples;    /* count of 16-bit samples */

    /* pdta arrays */
    const sf2_phdr_t *phdr;  int phdr_count;
    const sf2_bag_t  *pbag;  int pbag_count;
    const sf2_gen_t  *pgen;  int pgen_count;
    const sf2_inst_t *inst;  int inst_count;
    const sf2_bag_t  *ibag;  int ibag_count;
    const sf2_gen_t  *igen;  int igen_count;
    const sf2_shdr_t *shdr;  int shdr_count;
} sf2_file_t;

/* Generator set -- each generator has a value and a "present" flag */
typedef struct {
    int16_t val[GEN_COUNT];
    uint8_t set[GEN_COUNT];
} genset_t;

/* Resolved zone before writing */
typedef struct {
    int       preset_slot;  /* 0..255 in OFSF preset index */
    genset_t  gens;
    int       shdr_index;   /* index into sf2 shdr array */
} resolved_zone_t;

/* Sample dedup entry */
typedef struct {
    int       shdr_index;
    uint32_t  start;        /* adjusted start in smpl (sample points) */
    uint32_t  length;       /* in sample points */
    uint32_t  out_offset;   /* byte offset in output sample blob */
} sample_entry_t;

/* ------------------------------------------------------------------ */
/* Globals                                                            */
/* ------------------------------------------------------------------ */

static sf2_file_t g_sf2;

/* Output zone list (dynamically grown) */
static resolved_zone_t *g_zones;
static int g_zone_count;
static int g_zone_cap;

/* Sample dedup table */
static sample_entry_t *g_samples;
static int g_sample_count;
static int g_sample_cap;

/* Options */
static uint32_t g_max_size  = 0;        /* 0 = no limit */
static uint32_t g_sample_rate = 0;      /* 0 = auto-detect from first sample */

/* ------------------------------------------------------------------ */
/* Helpers                                                            */
/* ------------------------------------------------------------------ */

static void die(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "sf2_to_ofsf: error: ");
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    va_end(ap);
    exit(1);
}

static uint32_t read_u32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint32_t fourcc(const char *s) {
    return (uint32_t)(uint8_t)s[0] | ((uint32_t)(uint8_t)s[1] << 8) |
           ((uint32_t)(uint8_t)s[2] << 16) | ((uint32_t)(uint8_t)s[3] << 24);
}

static void zone_grow(void)
{
    if (g_zone_count >= g_zone_cap) {
        g_zone_cap = g_zone_cap ? g_zone_cap * 2 : 1024;
        g_zones = realloc(g_zones, (size_t)g_zone_cap * sizeof(g_zones[0]));
        if (!g_zones) die("out of memory allocating zones");
    }
}

static void sample_grow(void)
{
    if (g_sample_count >= g_sample_cap) {
        g_sample_cap = g_sample_cap ? g_sample_cap * 2 : 256;
        g_samples = realloc(g_samples, (size_t)g_sample_cap * sizeof(g_samples[0]));
        if (!g_samples) die("out of memory allocating sample table");
    }
}

/* ------------------------------------------------------------------ */
/* File I/O                                                           */
/* ------------------------------------------------------------------ */

static void sf2_open(const char *path)
{
    memset(&g_sf2, 0, sizeof(g_sf2));

#if USE_MMAP
    int fd = open(path, O_RDONLY);
    if (fd < 0)
        die("cannot open '%s': %s", path, strerror(errno));

    struct stat st;
    if (fstat(fd, &st) < 0) {
        close(fd);
        die("cannot stat '%s': %s", path, strerror(errno));
    }
    g_sf2.size = (size_t)st.st_size;
    if (g_sf2.size < 12)
        die("'%s' is too small to be an SF2 file (%zu bytes)", path, g_sf2.size);

    g_sf2.data = mmap(NULL, g_sf2.size, PROT_READ, MAP_PRIVATE, fd, 0);
    close(fd);
    if (g_sf2.data == MAP_FAILED)
        die("mmap failed for '%s': %s", path, strerror(errno));
    g_sf2.use_mmap = 1;
#else
    FILE *f = fopen(path, "rb");
    if (!f)
        die("cannot open '%s': %s", path, strerror(errno));
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    if (len < 12) {
        fclose(f);
        die("'%s' is too small to be an SF2 file (%ld bytes)", path, len);
    }
    g_sf2.size = (size_t)len;
    g_sf2.data = malloc(g_sf2.size);
    if (!g_sf2.data) {
        fclose(f);
        die("out of memory reading '%s'", path);
    }
    fseek(f, 0, SEEK_SET);
    if (fread(g_sf2.data, 1, g_sf2.size, f) != g_sf2.size) {
        fclose(f);
        die("short read on '%s'", path);
    }
    fclose(f);
    g_sf2.use_mmap = 0;
#endif
}

static void sf2_close(void)
{
#if USE_MMAP
    if (g_sf2.use_mmap && g_sf2.data)
        munmap(g_sf2.data, g_sf2.size);
#else
    free(g_sf2.data);
#endif
    memset(&g_sf2, 0, sizeof(g_sf2));
}

/* ------------------------------------------------------------------ */
/* RIFF / SF2 parsing                                                 */
/* ------------------------------------------------------------------ */

/* Find a chunk within [start, end). Returns pointer to chunk data and
   sets *chunk_size. Returns NULL if not found. */
static const uint8_t *find_chunk(uint32_t id, const uint8_t *start,
                                 const uint8_t *end, uint32_t *chunk_size)
{
    const uint8_t *p = start;
    while (p + 8 <= end) {
        uint32_t ck_id   = read_u32(p);
        uint32_t ck_size = read_u32(p + 4);
        if (p + 8 + ck_size > end)
            break;
        if (ck_id == id) {
            *chunk_size = ck_size;
            return p + 8;
        }
        /* Chunks are word-aligned */
        p += 8 + ((ck_size + 1) & ~1u);
    }
    return NULL;
}

/* Find a LIST chunk with the given form type. Returns pointer to the
   content (past the form type) and sets *list_size to the content size. */
static const uint8_t *find_list(uint32_t form_type, const uint8_t *start,
                                const uint8_t *end, uint32_t *list_size)
{
    const uint8_t *p = start;
    while (p + 8 <= end) {
        uint32_t ck_id   = read_u32(p);
        uint32_t ck_size = read_u32(p + 4);
        if (p + 8 + ck_size > end)
            break;
        if (ck_id == fourcc("LIST") && ck_size >= 4) {
            uint32_t ft = read_u32(p + 8);
            if (ft == form_type) {
                *list_size = ck_size - 4;
                return p + 12;
            }
        }
        p += 8 + ((ck_size + 1) & ~1u);
    }
    return NULL;
}

static void parse_sf2(void)
{
    const uint8_t *d = g_sf2.data;
    size_t sz = g_sf2.size;

    /* Verify RIFF header */
    if (sz < 12)
        die("file too small for RIFF header");
    if (read_u32(d) != fourcc("RIFF"))
        die("not a RIFF file");
    uint32_t riff_size = read_u32(d + 4);
    if ((size_t)riff_size + 8 > sz)
        die("RIFF size exceeds file size");
    if (read_u32(d + 8) != fourcc("sfbk"))
        die("not an SF2 file (expected 'sfbk' form type)");

    const uint8_t *riff_start = d + 12;
    const uint8_t *riff_end   = d + 8 + riff_size;

    /* Find sdta LIST */
    uint32_t sdta_size;
    const uint8_t *sdta = find_list(fourcc("sdta"), riff_start, riff_end, &sdta_size);
    if (!sdta)
        die("missing sdta LIST chunk");

    /* Find smpl within sdta */
    uint32_t smpl_size;
    const uint8_t *smpl = find_chunk(fourcc("smpl"), sdta, sdta + sdta_size, &smpl_size);
    if (!smpl)
        die("missing smpl chunk in sdta");
    g_sf2.smpl = (const int16_t *)smpl;
    g_sf2.smpl_samples = smpl_size / 2;

    /* Find pdta LIST */
    uint32_t pdta_size;
    const uint8_t *pdta = find_list(fourcc("pdta"), riff_start, riff_end, &pdta_size);
    if (!pdta)
        die("missing pdta LIST chunk");

    const uint8_t *pdta_end = pdta + pdta_size;

    /* Parse each pdta sub-chunk */
    uint32_t cs;
    const uint8_t *p;

#define PARSE_PDTA(tag, field, type) do {                                    \
    p = find_chunk(fourcc(tag), pdta, pdta_end, &cs);                        \
    if (!p) die("missing " tag " chunk in pdta");                            \
    if (cs % sizeof(type) != 0)                                              \
        die(tag " chunk size %u not a multiple of record size %zu",          \
            cs, sizeof(type));                                               \
    g_sf2.field = (const type *)p;                                           \
    g_sf2.field##_count = (int)(cs / sizeof(type));                          \
} while(0)

    PARSE_PDTA("phdr", phdr, sf2_phdr_t);
    PARSE_PDTA("pbag", pbag, sf2_bag_t);
    PARSE_PDTA("pgen", pgen, sf2_gen_t);
    PARSE_PDTA("inst", inst, sf2_inst_t);
    PARSE_PDTA("ibag", ibag, sf2_bag_t);
    PARSE_PDTA("igen", igen, sf2_gen_t);
    PARSE_PDTA("shdr", shdr, sf2_shdr_t);

#undef PARSE_PDTA

    /* Sanity checks */
    if (g_sf2.phdr_count < 2)
        die("phdr must have at least 2 records (last is terminal)");
    if (g_sf2.inst_count < 2)
        die("inst must have at least 2 records (last is terminal)");
    if (g_sf2.shdr_count < 1)
        die("shdr must have at least 1 record");

    fprintf(stderr, "SF2: %d presets, %d instruments, %d samples, %u sample points\n",
            g_sf2.phdr_count - 1, g_sf2.inst_count - 1,
            g_sf2.shdr_count - 1, g_sf2.smpl_samples);
}

/* ------------------------------------------------------------------ */
/* Generator defaults (SF2 spec Table 15)                             */
/* ------------------------------------------------------------------ */

static void genset_init_defaults(genset_t *gs)
{
    memset(gs, 0, sizeof(*gs));

    /* Key range and velocity range default to full */
    gs->val[GEN_keyRange]       = (int16_t)(0 | (127 << 8));   /* lo=0, hi=127 */
    gs->val[GEN_velRange]       = (int16_t)(0 | (127 << 8));
    gs->set[GEN_keyRange]       = 1;
    gs->set[GEN_velRange]       = 1;

    /* Filter defaults */
    gs->val[GEN_initialFilterFc] = 13500;
    gs->val[GEN_initialFilterQ]  = 0;

    /* Scale tuning */
    gs->val[GEN_scaleTuning]    = 100;

    /* Envelope defaults: -12000 timecents = essentially instantaneous */
    gs->val[GEN_delayVolEnv]    = -12000;
    gs->val[GEN_attackVolEnv]   = -12000;
    gs->val[GEN_holdVolEnv]     = -12000;
    gs->val[GEN_decayVolEnv]    = -12000;
    gs->val[GEN_sustainVolEnv]  = 0;       /* full sustain (0 cB attenuation) */
    gs->val[GEN_releaseVolEnv]  = -12000;

    gs->val[GEN_delayModEnv]    = -12000;
    gs->val[GEN_attackModEnv]   = -12000;
    gs->val[GEN_holdModEnv]     = -12000;
    gs->val[GEN_decayModEnv]    = -12000;
    gs->val[GEN_sustainModEnv]  = 0;
    gs->val[GEN_releaseModEnv]  = -12000;

    /* LFO defaults */
    gs->val[GEN_delayModLFO]    = -12000;
    gs->val[GEN_freqModLFO]     = 0;       /* ~8.176 Hz */
    gs->val[GEN_delayVibLFO]    = -12000;
    gs->val[GEN_freqVibLFO]     = 0;

    /* Pan, attenuation */
    gs->val[GEN_pan]            = 0;
    gs->val[GEN_initialAttenuation] = 0;

    /* Pitch modulation */
    gs->val[GEN_modLfoToPitch]  = 0;
    gs->val[GEN_vibLfoToPitch]  = 0;
    gs->val[GEN_modEnvToPitch]  = 0;

    /* Filter modulation */
    gs->val[GEN_modLfoToFilterFc] = 0;
    gs->val[GEN_modEnvToFilterFc] = 0;

    /* Volume modulation */
    gs->val[GEN_modLfoToVolume] = 0;

    /* Tuning */
    gs->val[GEN_coarseTune]     = 0;
    gs->val[GEN_fineTune]       = 0;

    /* Sample modes */
    gs->val[GEN_sampleModes]    = 0;

    /* Exclusive class */
    gs->val[GEN_exclusiveClass] = 0;

    /* Root key override: -1 means "use sample header value" */
    gs->val[GEN_overridingRootKey] = -1;
}

/* Apply generators from an SF2 generator range to a genset.
   is_preset: if true, these are preset-level generators (additive for most). */
static void genset_apply(genset_t *gs, const sf2_gen_t *gens, int count,
                         int is_preset)
{
    for (int i = 0; i < count; i++) {
        uint16_t oper = gens[i].oper;
        int16_t  val  = (int16_t)gens[i].amount;

        if (oper >= GEN_COUNT)
            continue;

        /* Skip non-zone generators that are only structural */
        if (oper == GEN_instrument)
            continue;

        if (is_preset) {
            /* Preset generators ADD to instrument values for most gens.
               Exceptions: keyRange and velRange are set directly (then
               intersected later). sampleID/sampleModes only come from
               instrument level. */
            if (oper == GEN_sampleID || oper == GEN_sampleModes)
                continue;
            if (oper == GEN_keyRange || oper == GEN_velRange) {
                gs->val[oper] = val;
                gs->set[oper] = 1;
            } else {
                gs->val[oper] = (int16_t)(gs->val[oper] + val);
                gs->set[oper] = 1;
            }
        } else {
            /* Instrument generators SET the value directly */
            gs->val[oper] = val;
            gs->set[oper] = 1;
        }
    }
}

/* Intersect key/velocity ranges: the result is the overlap of two ranges.
   Each range is stored as lo in low byte, hi in high byte. */
static int16_t range_intersect(int16_t a, int16_t b)
{
    uint8_t a_lo = (uint8_t)((uint16_t)a & 0xFF);
    uint8_t a_hi = (uint8_t)(((uint16_t)a >> 8) & 0xFF);
    uint8_t b_lo = (uint8_t)((uint16_t)b & 0xFF);
    uint8_t b_hi = (uint8_t)(((uint16_t)b >> 8) & 0xFF);

    uint8_t lo = a_lo > b_lo ? a_lo : b_lo;
    uint8_t hi = a_hi < b_hi ? a_hi : b_hi;

    if (lo > hi)
        return -1;  /* empty range */

    return (int16_t)((uint16_t)lo | ((uint16_t)hi << 8));
}

/* ------------------------------------------------------------------ */
/* Preset resolution                                                  */
/* ------------------------------------------------------------------ */

/* Check if a generator list contains a terminal generator of the given type */
static int has_terminal_gen(const sf2_gen_t *gens, int count, uint16_t oper)
{
    return count > 0 && gens[count - 1].oper == oper;
}

/* Resolve one instrument zone and append to g_zones */
static void resolve_instrument_zone(int preset_slot,
                                    const genset_t *preset_global,
                                    const genset_t *preset_zone_gens,
                                    int has_preset_zone,
                                    const genset_t *inst_global,
                                    const sf2_gen_t *iz_gens, int iz_gen_count)
{
    /* Check for sampleID terminal generator */
    if (!has_terminal_gen(iz_gens, iz_gen_count, GEN_sampleID))
        return;  /* not a real zone, skip */

    int sample_id = (int)(uint16_t)iz_gens[iz_gen_count - 1].amount;
    if (sample_id < 0 || sample_id >= g_sf2.shdr_count - 1) {
        fprintf(stderr, "  warning: sampleID %d out of range, skipping zone\n",
                sample_id);
        return;
    }

    /* Check sample type -- skip ROM samples and linked stereo */
    uint16_t stype = g_sf2.shdr[sample_id].sample_type;
    if (stype & 0x8000) {
        /* ROM sample, skip */
        return;
    }
    /* Accept mono (1), left (2), right (4), linked (8) samples;
       for stereo pairs we only take each individual channel zone. */

    /* Start with defaults */
    genset_t merged;
    genset_init_defaults(&merged);

    /* Apply instrument global zone (if any) */
    if (inst_global) {
        for (int g = 0; g < GEN_COUNT; g++) {
            if (inst_global->set[g]) {
                /* Global instrument gens set the value */
                merged.val[g] = inst_global->val[g];
                merged.set[g] = 1;
            }
        }
    }

    /* Apply instrument zone generators (they override globals/defaults) */
    genset_apply(&merged, iz_gens, iz_gen_count, 0);

    /* Save instrument-level key/vel ranges before preset merging */
    int16_t inst_key_range = merged.val[GEN_keyRange];
    int16_t inst_vel_range = merged.val[GEN_velRange];

    /* Apply preset global zone */
    if (preset_global) {
        for (int g = 0; g < GEN_COUNT; g++) {
            if (!preset_global->set[g])
                continue;
            if (g == GEN_sampleID || g == GEN_sampleModes)
                continue;
            if (g == GEN_keyRange || g == GEN_velRange) {
                /* Will intersect below */
            } else {
                merged.val[g] = (int16_t)(merged.val[g] + preset_global->val[g]);
            }
        }
    }

    /* Apply preset zone generators (additive except ranges) */
    if (has_preset_zone) {
        for (int g = 0; g < GEN_COUNT; g++) {
            if (!preset_zone_gens->set[g])
                continue;
            if (g == GEN_sampleID || g == GEN_sampleModes)
                continue;
            if (g == GEN_keyRange || g == GEN_velRange) {
                /* Will intersect below */
            } else {
                merged.val[g] = (int16_t)(merged.val[g] + preset_zone_gens->val[g]);
            }
        }
    }

    /* Intersect key/velocity ranges from preset and instrument levels */
    int16_t final_key = inst_key_range;
    int16_t final_vel = inst_vel_range;

    if (preset_global && preset_global->set[GEN_keyRange])
        final_key = range_intersect(final_key, preset_global->val[GEN_keyRange]);
    if (has_preset_zone && preset_zone_gens->set[GEN_keyRange])
        final_key = range_intersect(final_key, preset_zone_gens->val[GEN_keyRange]);

    if (preset_global && preset_global->set[GEN_velRange])
        final_vel = range_intersect(final_vel, preset_global->val[GEN_velRange]);
    if (has_preset_zone && preset_zone_gens->set[GEN_velRange])
        final_vel = range_intersect(final_vel, preset_zone_gens->val[GEN_velRange]);

    /* If ranges don't overlap, skip this zone */
    if (final_key == -1 || final_vel == -1)
        return;

    merged.val[GEN_keyRange] = final_key;
    merged.val[GEN_velRange] = final_vel;

    /* Append zone */
    zone_grow();
    resolved_zone_t *rz = &g_zones[g_zone_count++];
    rz->preset_slot = preset_slot;
    rz->gens        = merged;
    rz->shdr_index  = sample_id;
}

/* Resolve one instrument (all its zones) */
static void resolve_instrument(int preset_slot,
                               const genset_t *preset_global,
                               const genset_t *preset_zone_gens,
                               int has_preset_zone,
                               int inst_index)
{
    if (inst_index < 0 || inst_index >= g_sf2.inst_count - 1) {
        fprintf(stderr, "  warning: instrument index %d out of range\n", inst_index);
        return;
    }

    int bag_start = g_sf2.inst[inst_index].bag_ndx;
    int bag_end   = g_sf2.inst[inst_index + 1].bag_ndx;

    if (bag_start < 0 || bag_end > g_sf2.ibag_count || bag_start >= bag_end) {
        fprintf(stderr, "  warning: invalid ibag range [%d, %d) for instrument %d\n",
                bag_start, bag_end, inst_index);
        return;
    }

    /* Check for instrument global zone */
    genset_t inst_global;
    int has_inst_global = 0;
    int first_zone_bag = bag_start;

    {
        int gen_start = g_sf2.ibag[bag_start].gen_ndx;
        int gen_end   = (bag_start + 1 < g_sf2.ibag_count)
                        ? g_sf2.ibag[bag_start + 1].gen_ndx
                        : g_sf2.igen_count;

        if (gen_start >= 0 && gen_end <= g_sf2.igen_count && gen_start <= gen_end) {
            int gen_count = gen_end - gen_start;
            if (!has_terminal_gen(&g_sf2.igen[gen_start], gen_count, GEN_sampleID)) {
                /* This is a global zone (no sampleID terminal) */
                genset_init_defaults(&inst_global);
                genset_apply(&inst_global, &g_sf2.igen[gen_start], gen_count, 0);
                has_inst_global = 1;
                first_zone_bag = bag_start + 1;
            }
        }
    }

    /* Process each instrument zone */
    for (int b = first_zone_bag; b < bag_end; b++) {
        int gen_start = g_sf2.ibag[b].gen_ndx;
        int gen_end   = (b + 1 < g_sf2.ibag_count)
                        ? g_sf2.ibag[b + 1].gen_ndx
                        : g_sf2.igen_count;

        if (gen_start < 0 || gen_end > g_sf2.igen_count || gen_start > gen_end)
            continue;

        int gen_count = gen_end - gen_start;
        resolve_instrument_zone(preset_slot,
                                preset_global, preset_zone_gens, has_preset_zone,
                                has_inst_global ? &inst_global : NULL,
                                &g_sf2.igen[gen_start], gen_count);
    }
}

/* Resolve one preset -> all its zones */
static void resolve_preset(int phdr_index)
{
    const sf2_phdr_t *ph = &g_sf2.phdr[phdr_index];

    /* Compute preset slot: bank 0 -> melodic 0..127, bank 128 -> drum 128..255 */
    int slot;
    if (ph->bank == 128) {
        slot = 128 + (ph->preset & 127);
    } else if (ph->bank == 0) {
        slot = ph->preset & 127;
    } else {
        /* We only support bank 0 and bank 128 in the 256-slot index.
           Map other banks to bank 0 with a warning. */
        fprintf(stderr, "  warning: preset '%.*s' bank %d mapped to bank 0 slot %d\n",
                20, ph->name, ph->bank, ph->preset & 127);
        slot = ph->preset & 127;
    }

    int bag_start = ph->bag_ndx;
    int bag_end   = g_sf2.phdr[phdr_index + 1].bag_ndx;

    if (bag_start < 0 || bag_end > g_sf2.pbag_count || bag_start >= bag_end)
        return;

    /* Check for preset global zone */
    genset_t preset_global;
    int has_preset_global = 0;
    int first_zone_bag = bag_start;

    {
        int gen_start = g_sf2.pbag[bag_start].gen_ndx;
        int gen_end   = (bag_start + 1 < g_sf2.pbag_count)
                        ? g_sf2.pbag[bag_start + 1].gen_ndx
                        : g_sf2.pgen_count;

        if (gen_start >= 0 && gen_end <= g_sf2.pgen_count && gen_start <= gen_end) {
            int gen_count = gen_end - gen_start;
            if (!has_terminal_gen(&g_sf2.pgen[gen_start], gen_count, GEN_instrument)) {
                /* Global zone */
                memset(&preset_global, 0, sizeof(preset_global));
                genset_apply(&preset_global, &g_sf2.pgen[gen_start], gen_count, 1);
                has_preset_global = 1;
                first_zone_bag = bag_start + 1;
            }
        }
    }

    /* Process each preset zone */
    for (int b = first_zone_bag; b < bag_end; b++) {
        int gen_start = g_sf2.pbag[b].gen_ndx;
        int gen_end   = (b + 1 < g_sf2.pbag_count)
                        ? g_sf2.pbag[b + 1].gen_ndx
                        : g_sf2.pgen_count;

        if (gen_start < 0 || gen_end > g_sf2.pgen_count || gen_start > gen_end)
            continue;

        int gen_count = gen_end - gen_start;
        const sf2_gen_t *gens = &g_sf2.pgen[gen_start];

        /* Must have an instrument terminal generator */
        if (!has_terminal_gen(gens, gen_count, GEN_instrument))
            continue;

        int inst_index = (int)(uint16_t)gens[gen_count - 1].amount;

        /* Collect preset zone generators (excluding the terminal instrument gen) */
        genset_t pz_gens;
        memset(&pz_gens, 0, sizeof(pz_gens));
        genset_apply(&pz_gens, gens, gen_count - 1, 1);

        resolve_instrument(slot,
                           has_preset_global ? &preset_global : NULL,
                           &pz_gens, 1,
                           inst_index);
    }
}

/* ------------------------------------------------------------------ */
/* Sample deduplication                                               */
/* ------------------------------------------------------------------ */

/* Find or add a sample entry. Returns the sample entry index. */
static int sample_dedup(int shdr_index, uint32_t start, uint32_t length)
{
    /* Linear search -- fine for typical SF2 sizes */
    for (int i = 0; i < g_sample_count; i++) {
        if (g_samples[i].shdr_index == shdr_index &&
            g_samples[i].start == start &&
            g_samples[i].length == length)
            return i;
    }

    sample_grow();
    int idx = g_sample_count++;
    g_samples[idx].shdr_index = shdr_index;
    g_samples[idx].start      = start;
    g_samples[idx].length     = length;
    g_samples[idx].out_offset = 0;  /* filled in later */
    return idx;
}

/* ------------------------------------------------------------------ */
/* Output generation                                                  */
/* ------------------------------------------------------------------ */

/* Comparison function for sorting zones by preset_slot, then key_lo */
static int zone_compare(const void *a, const void *b)
{
    const resolved_zone_t *za = (const resolved_zone_t *)a;
    const resolved_zone_t *zb = (const resolved_zone_t *)b;

    if (za->preset_slot != zb->preset_slot)
        return za->preset_slot - zb->preset_slot;

    /* Within the same preset, sort by key_lo then vel_lo */
    uint8_t ka = (uint8_t)((uint16_t)za->gens.val[GEN_keyRange] & 0xFF);
    uint8_t kb = (uint8_t)((uint16_t)zb->gens.val[GEN_keyRange] & 0xFF);
    if (ka != kb)
        return (int)ka - (int)kb;

    uint8_t va = (uint8_t)((uint16_t)za->gens.val[GEN_velRange] & 0xFF);
    uint8_t vb = (uint8_t)((uint16_t)zb->gens.val[GEN_velRange] & 0xFF);
    return (int)va - (int)vb;
}

static void write_output(const char *path)
{
    /* Sort zones by preset slot */
    if (g_zone_count > 0)
        qsort(g_zones, (size_t)g_zone_count, sizeof(g_zones[0]), zone_compare);

    /* Phase 1: Compute sample addresses for each zone and build dedup table */
    int *zone_sample_idx = calloc((size_t)g_zone_count, sizeof(int));
    if (!zone_sample_idx && g_zone_count > 0)
        die("out of memory");

    for (int i = 0; i < g_zone_count; i++) {
        resolved_zone_t *rz = &g_zones[i];
        int si = rz->shdr_index;
        const sf2_shdr_t *sh = &g_sf2.shdr[si];

        /* Compute adjusted sample addresses with generator offsets */
        int32_t start_offset = rz->gens.val[GEN_startAddrsOffset]
                             + rz->gens.val[GEN_startAddrsCoarseOffset] * 32768;
        int32_t end_offset   = rz->gens.val[GEN_endAddrsOffset]
                             + rz->gens.val[GEN_endAddrsCoarseOffset] * 32768;

        uint32_t abs_start = (uint32_t)((int32_t)sh->start + start_offset);
        uint32_t abs_end   = (uint32_t)((int32_t)sh->end   + end_offset);

        /* Clamp to sample data bounds */
        if (abs_start >= g_sf2.smpl_samples)
            abs_start = g_sf2.smpl_samples > 0 ? g_sf2.smpl_samples - 1 : 0;
        if (abs_end > g_sf2.smpl_samples)
            abs_end = g_sf2.smpl_samples;
        if (abs_end <= abs_start)
            abs_end = abs_start + 1;

        uint32_t length = abs_end - abs_start;
        zone_sample_idx[i] = sample_dedup(si, abs_start, length);
    }

    /* Phase 2: Assign output offsets to each unique sample */
    uint32_t sample_blob_size = 0;
    for (int i = 0; i < g_sample_count; i++) {
        g_samples[i].out_offset = sample_blob_size;
        /* Each sample is 16-bit = 2 bytes */
        sample_blob_size += g_samples[i].length * 2;
        /* Align to 4 bytes */
        sample_blob_size = (sample_blob_size + 3) & ~3u;
    }

    /* Compute total file size */
    uint32_t header_size  = sizeof(ofsf_header_t);
    uint32_t index_size   = OFSF_PRESET_COUNT * sizeof(ofsf_preset_t);
    uint32_t zones_size   = (uint32_t)g_zone_count * sizeof(ofsf_zone_t);
    uint32_t sample_data_offset = header_size + index_size + zones_size;
    /* Align sample data offset to 4 bytes */
    sample_data_offset = (sample_data_offset + 3) & ~3u;
    uint32_t total_size = sample_data_offset + sample_blob_size;

    /* Check max size */
    if (g_max_size > 0 && total_size > g_max_size)
        die("output size %u bytes exceeds --max-size=%u", total_size, g_max_size);

    /* Determine sample rate */
    uint32_t sample_rate = g_sample_rate;
    if (sample_rate == 0 && g_zone_count > 0) {
        sample_rate = g_sf2.shdr[g_zones[0].shdr_index].sample_rate;
    }
    if (sample_rate == 0)
        sample_rate = 44100;

    /* Phase 3: Build preset index */
    ofsf_preset_t preset_index[OFSF_PRESET_COUNT];
    memset(preset_index, 0, sizeof(preset_index));

    if (g_zone_count > 0) {
        int cur_slot = g_zones[0].preset_slot;
        int cur_start = 0;

        for (int i = 1; i <= g_zone_count; i++) {
            int slot = (i < g_zone_count) ? g_zones[i].preset_slot : -1;
            if (slot != cur_slot) {
                if (cur_slot >= 0 && cur_slot < OFSF_PRESET_COUNT) {
                    preset_index[cur_slot].zone_start = (uint16_t)cur_start;
                    preset_index[cur_slot].zone_count = (uint16_t)(i - cur_start);
                }
                cur_slot = slot;
                cur_start = i;
            }
        }
    }

    /* Phase 4: Build output zone structs */
    ofsf_zone_t *out_zones = calloc((size_t)g_zone_count, sizeof(ofsf_zone_t));
    if (!out_zones && g_zone_count > 0)
        die("out of memory");

    for (int i = 0; i < g_zone_count; i++) {
        resolved_zone_t *rz = &g_zones[i];
        ofsf_zone_t *oz = &out_zones[i];
        const genset_t *g = &rz->gens;
        const sf2_shdr_t *sh = &g_sf2.shdr[rz->shdr_index];
        sample_entry_t *se = &g_samples[zone_sample_idx[i]];

        /* Key/velocity range */
        uint16_t kr = (uint16_t)g->val[GEN_keyRange];
        uint16_t vr = (uint16_t)g->val[GEN_velRange];
        oz->key_lo = (uint8_t)(kr & 0xFF);
        oz->key_hi = (uint8_t)((kr >> 8) & 0xFF);
        oz->vel_lo = (uint8_t)(vr & 0xFF);
        oz->vel_hi = (uint8_t)((vr >> 8) & 0xFF);

        /* Sample reference */
        oz->sample_offset = se->out_offset;
        oz->sample_length = se->length;

        /* Loop points: relative to the sample data we copy */
        int32_t loop_start_offset = g->val[GEN_startloopAddrsOffset]
                                  + g->val[GEN_startloopAddrsCoarseOffset] * 32768;
        int32_t loop_end_offset   = g->val[GEN_endloopAddrsOffset]
                                  + g->val[GEN_endloopAddrsCoarseOffset] * 32768;

        uint32_t abs_loop_start = (uint32_t)((int32_t)sh->loop_start + loop_start_offset);
        uint32_t abs_loop_end   = (uint32_t)((int32_t)sh->loop_end   + loop_end_offset);

        /* Make loop points relative to sample start */
        if (abs_loop_start >= se->start)
            oz->loop_start = abs_loop_start - se->start;
        else
            oz->loop_start = 0;
        if (abs_loop_end >= se->start)
            oz->loop_end = abs_loop_end - se->start;
        else
            oz->loop_end = 0;

        /* Clamp loop points to sample length */
        if (oz->loop_start >= se->length)
            oz->loop_start = se->length > 0 ? se->length - 1 : 0;
        if (oz->loop_end > se->length)
            oz->loop_end = se->length;
        if (oz->loop_end <= oz->loop_start)
            oz->loop_end = oz->loop_start + 1;

        /* Loop mode */
        int16_t sm = g->val[GEN_sampleModes];
        switch (sm & 3) {
        case 0:  oz->loop_mode = OFSF_LOOP_NONE;    break;
        case 1:  oz->loop_mode = OFSF_LOOP_FORWARD;  break;
        case 2:  oz->loop_mode = OFSF_LOOP_NONE;     break; /* reserved */
        case 3:  oz->loop_mode = OFSF_LOOP_FORWARD;  break; /* loop+release */
        }

        /* Root key */
        if (g->val[GEN_overridingRootKey] >= 0 && g->val[GEN_overridingRootKey] <= 127)
            oz->root_key = (uint8_t)g->val[GEN_overridingRootKey];
        else
            oz->root_key = sh->original_key;

        /* Tuning */
        oz->fine_tune = (int8_t)g->val[GEN_fineTune];
        oz->coarse_tune = (int8_t)g->val[GEN_coarseTune];

        /* Add sample header correction to fine tune */
        int total_fine = (int)oz->fine_tune + (int)sh->correction;
        if (total_fine > 99) total_fine = 99;
        if (total_fine < -99) total_fine = -99;
        oz->fine_tune = (int8_t)total_fine;

        /* Volume envelope */
        oz->vol_delay   = g->val[GEN_delayVolEnv];
        oz->vol_attack  = g->val[GEN_attackVolEnv];
        oz->vol_hold    = g->val[GEN_holdVolEnv];
        oz->vol_decay   = g->val[GEN_decayVolEnv];
        oz->vol_sustain = g->val[GEN_sustainVolEnv];
        oz->vol_release = g->val[GEN_releaseVolEnv];

        /* Modulation envelope */
        oz->mod_delay   = g->val[GEN_delayModEnv];
        oz->mod_attack  = g->val[GEN_attackModEnv];
        oz->mod_hold    = g->val[GEN_holdModEnv];
        oz->mod_decay   = g->val[GEN_decayModEnv];
        oz->mod_sustain = g->val[GEN_sustainModEnv];
        oz->mod_release = g->val[GEN_releaseModEnv];
        oz->mod_env_to_pitch  = g->val[GEN_modEnvToPitch];
        oz->mod_env_to_filter = g->val[GEN_modEnvToFilterFc];

        /* Modulation LFO */
        oz->mod_lfo_delay     = g->val[GEN_delayModLFO];
        oz->mod_lfo_freq      = g->val[GEN_freqModLFO];
        oz->mod_lfo_to_pitch  = g->val[GEN_modLfoToPitch];
        oz->mod_lfo_to_filter = g->val[GEN_modLfoToFilterFc];
        oz->mod_lfo_to_volume = g->val[GEN_modLfoToVolume];

        /* Vibrato LFO */
        oz->vib_lfo_delay     = g->val[GEN_delayVibLFO];
        oz->vib_lfo_freq      = g->val[GEN_freqVibLFO];
        oz->vib_lfo_to_pitch  = g->val[GEN_vibLfoToPitch];

        /* Filter */
        oz->initial_fc = g->val[GEN_initialFilterFc];
        oz->initial_q  = g->val[GEN_initialFilterQ];

        /* Output */
        oz->initial_attn = g->val[GEN_initialAttenuation];
        oz->pan          = g->val[GEN_pan];

        /* Exclusive class */
        oz->exclusive_class = (uint8_t)g->val[GEN_exclusiveClass];

        /* Padding */
        memset(oz->_pad, 0, sizeof(oz->_pad));
    }

    /* Phase 5: Write the file */
    FILE *f = fopen(path, "wb");
    if (!f)
        die("cannot create '%s': %s", path, strerror(errno));

    /* Header */
    ofsf_header_t hdr;
    memset(&hdr, 0, sizeof(hdr));
    hdr.magic             = OFSF_MAGIC;
    hdr.version           = OFSF_VERSION;
    hdr.sample_rate       = sample_rate;
    hdr.zone_count        = (uint32_t)g_zone_count;
    hdr.sample_data_offset = sample_data_offset;
    hdr.sample_data_size  = sample_blob_size;
    hdr.flags             = 0;
    hdr.reserved          = 0;

    if (fwrite(&hdr, sizeof(hdr), 1, f) != 1)
        die("write error on header");

    /* Preset index */
    if (fwrite(preset_index, sizeof(preset_index), 1, f) != 1)
        die("write error on preset index");

    /* Zones */
    if (g_zone_count > 0) {
        if (fwrite(out_zones, sizeof(ofsf_zone_t), (size_t)g_zone_count, f)
            != (size_t)g_zone_count)
            die("write error on zones");
    }

    /* Pad to sample data offset */
    long cur_pos = ftell(f);
    if (cur_pos < 0)
        die("ftell failed");
    while ((uint32_t)cur_pos < sample_data_offset) {
        uint8_t zero = 0;
        fwrite(&zero, 1, 1, f);
        cur_pos++;
    }

    /* Sample data */
    for (int i = 0; i < g_sample_count; i++) {
        const sample_entry_t *se = &g_samples[i];
        uint32_t start = se->start;
        uint32_t length = se->length;

        /* Bounds check */
        if (start + length > g_sf2.smpl_samples) {
            fprintf(stderr, "  warning: sample %d overruns smpl data, clamping\n", i);
            if (start >= g_sf2.smpl_samples)
                length = 0;
            else
                length = g_sf2.smpl_samples - start;
        }

        if (length > 0) {
            if (fwrite(&g_sf2.smpl[start], 2, length, f) != length)
                die("write error on sample data");
        }

        /* Pad to 4-byte alignment */
        uint32_t written = length * 2;
        uint32_t aligned = (written + 3) & ~3u;
        uint32_t pad = aligned - written;
        if (pad > 0) {
            uint8_t zeros[4] = {0};
            fwrite(zeros, 1, pad, f);
        }
    }

    fclose(f);

    /* Summary */
    fprintf(stderr, "Output: %s\n", path);
    fprintf(stderr, "  zones:       %d\n", g_zone_count);
    fprintf(stderr, "  samples:     %d unique\n", g_sample_count);
    fprintf(stderr, "  sample data: %u bytes (%.1f MB)\n",
            sample_blob_size, (double)sample_blob_size / (1024.0 * 1024.0));
    fprintf(stderr, "  total size:  %u bytes (%.1f MB)\n",
            total_size, (double)total_size / (1024.0 * 1024.0));
    fprintf(stderr, "  sample rate: %u Hz\n", sample_rate);

    /* Print preset occupancy */
    int melodic_count = 0, drum_count = 0;
    for (int i = 0; i < 128; i++) {
        if (preset_index[i].zone_count > 0) melodic_count++;
    }
    for (int i = 128; i < 256; i++) {
        if (preset_index[i].zone_count > 0) drum_count++;
    }
    fprintf(stderr, "  presets:     %d melodic, %d drum\n", melodic_count, drum_count);

    free(out_zones);
    free(zone_sample_idx);
}

/* ------------------------------------------------------------------ */
/* Argument parsing                                                   */
/* ------------------------------------------------------------------ */

static uint32_t parse_size(const char *s)
{
    char *end;
    double val = strtod(s, &end);
    if (end == s)
        die("invalid size: '%s'", s);
    if (*end == 'k' || *end == 'K')
        val *= 1024;
    else if (*end == 'm' || *end == 'M')
        val *= 1024 * 1024;
    else if (*end == 'g' || *end == 'G')
        val *= 1024 * 1024 * 1024;
    else if (*end != '\0')
        die("invalid size suffix: '%c'", *end);
    return (uint32_t)val;
}

static void usage(void)
{
    fprintf(stderr,
        "Usage: sf2_to_ofsf input.sf2 output.ofsf [options]\n"
        "\n"
        "Options:\n"
        "  --max-size=SIZE    Maximum output file size (e.g. 8M, 512K)\n"
        "  --sample-rate=RATE Override sample rate (default: from SF2)\n"
        "  -h, --help         Show this help\n"
        "\n"
        "Converts an SF2 soundfont to the openfpgaOS .ofsf runtime format.\n"
    );
    exit(1);
}

int main(int argc, char **argv)
{
    const char *input_path = NULL;
    const char *output_path = NULL;

    for (int i = 1; i < argc; i++) {
        if (strncmp(argv[i], "--max-size=", 11) == 0) {
            g_max_size = parse_size(argv[i] + 11);
        } else if (strncmp(argv[i], "--sample-rate=", 14) == 0) {
            g_sample_rate = (uint32_t)atoi(argv[i] + 14);
            if (g_sample_rate == 0)
                die("invalid sample rate: '%s'", argv[i] + 14);
        } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            usage();
        } else if (argv[i][0] == '-') {
            die("unknown option: '%s'", argv[i]);
        } else if (!input_path) {
            input_path = argv[i];
        } else if (!output_path) {
            output_path = argv[i];
        } else {
            die("unexpected argument: '%s'", argv[i]);
        }
    }

    if (!input_path || !output_path)
        usage();

    /* Open and parse SF2 */
    sf2_open(input_path);
    parse_sf2();

    /* Resolve all presets */
    for (int i = 0; i < g_sf2.phdr_count - 1; i++) {
        resolve_preset(i);
    }

    fprintf(stderr, "Resolved %d zones from %d presets\n",
            g_zone_count, g_sf2.phdr_count - 1);

    /* Write output */
    write_output(output_path);

    /* Cleanup */
    sf2_close();
    free(g_zones);
    free(g_samples);

    return 0;
}
