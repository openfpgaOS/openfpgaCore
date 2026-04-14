/*
 * of_smp_bank.c -- .ofsf runtime bank loader
 *
 * Loads a pre-built .ofsf bank from SD into the CRAM1 sample pool.
 * No parsing beyond header validation — the zone table and samples
 * are already in their final layout.
 */

#include "include/of_smp_bank.h"
#include "include/of_mixer.h"
#include "include/of_cache.h"
#include "include/of_services.h"

#include <string.h>
#include <stdio.h>

static const ofsf_header_t *loaded_header;
static const ofsf_preset_t *loaded_presets;
static const ofsf_zone_t   *loaded_zones;
static const void           *sample_base;   /* absolute CRAM1 address of sample blob */
static int                   bank_loaded;

int of_smp_bank_load(const char *path)
{
    if (bank_loaded)
        of_smp_bank_unload();

    FILE *f = fopen(path, "rb");
    if (!f) return -10;
    fseek(f, 0, SEEK_END);
    long fsize = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (fsize <= 0) { fclose(f); return -11; }

    /* Allocate from CRAM1 — the mixer hardware can only DMA from CRAM1 */
    void *buf = of_mixer_alloc_samples((uint32_t)fsize);
    if (!buf) { fclose(f); return -2; }

    /* Read entire file into CRAM1 */
    uint8_t *dst = (uint8_t *)buf;
    long remaining = fsize;
    while (remaining > 0) {
        long n = (long)fread(dst, 1, remaining > 4096 ? 4096 : remaining, f);
        if (n <= 0) { fclose(f); return -4; }
        dst += n;
        remaining -= n;
    }
    fclose(f);

    /* Flush D-cache — CRAM1 cached writes must reach SRAM before
     * the mixer hardware DMA reads them. */
    of_cache_flush();

    /* Validate header */
    const ofsf_header_t *hdr = (const ofsf_header_t *)buf;
    if (hdr->magic != OFSF_MAGIC || hdr->version != OFSF_VERSION)
        return -5;

    /* Set up pointers into the loaded data */
    const uint8_t *base = (const uint8_t *)buf;
    loaded_header  = hdr;
    loaded_presets = (const ofsf_preset_t *)(base + sizeof(ofsf_header_t));
    loaded_zones   = (const ofsf_zone_t *)(base + sizeof(ofsf_header_t) +
                      OFSF_PRESET_COUNT * sizeof(ofsf_preset_t));
    sample_base    = base + hdr->sample_data_offset;

    bank_loaded = 1;
    return 0;
}

void of_smp_bank_unload(void)
{
    loaded_header  = 0;
    loaded_presets = 0;
    loaded_zones   = 0;
    sample_base    = 0;
    bank_loaded    = 0;
    of_mixer_free_samples();
}

const ofsf_header_t *of_smp_bank_get(void)
{
    return loaded_header;
}

const void *of_smp_bank_sample_base(void)
{
    return sample_base;
}

int of_smp_zone_lookup(int bank, int program, int key, int velocity,
                       const ofsf_zone_t **zones_out, int max_zones)
{
    if (!bank_loaded || !loaded_presets || !loaded_zones)
        return 0;
    if (program < 0 || program > 127)
        return 0;

    /* Preset index: bank 0 = slots 0..127, bank 128 (drums) = slots 128..255 */
    int idx;
    if (bank == 128)
        idx = 128 + program;
    else
        idx = program; /* bank 0 only; other banks not yet supported */

    if (idx < 0 || idx >= OFSF_PRESET_COUNT)
        return 0;

    const ofsf_preset_t *pr = &loaded_presets[idx];
    if (pr->zone_count == 0)
        return 0;

    int found = 0;
    for (int i = 0; i < pr->zone_count && found < max_zones; i++) {
        int zi = pr->zone_start + i;
        if ((uint32_t)zi >= loaded_header->zone_count)
            break;
        const ofsf_zone_t *z = &loaded_zones[zi];
        if (key >= z->key_lo && key <= z->key_hi &&
            velocity >= z->vel_lo && velocity <= z->vel_hi) {
            zones_out[found++] = z;
        }
    }
    return found;
}
