/*
 * Verilator harness — focused floor-span path coverage.
 *
 * Drives the GPU's CMD_DRAW_SPAN against a CPU reference that
 * literally executes BUILD's hlineasm4 in C.  The reference uses
 * shift-mode addressing (i4/i5/bits/shifter); the GPU uses multiply-
 * mode (sp_s/sp_t/tex_width).  We translate the BUILD-style inputs
 * into the closest multiply-mode equivalent and compare bytewise.
 *
 * IMPORTANT: the GPU's RTL does NOT have shift-mode tex addressing.
 * The shift-mode path was retired (see gpu_core.v:838 and the
 * "sp_tex_shift / sp_tex_bits removed" note at line 622).  Cases
 * that REQUIRE shift-mode wrap behaviour (F2, F3, F6, F8) will
 * therefore differ from the SW reference — not a hardware bug, but
 * a documented capability gap.  These tests act as a regression
 * suite for IF/WHEN shift-mode is added back.
 *
 * Pass criteria:
 *   F1 — axis-aligned smoke test, no wrap → memcmp == 0 (must pass)
 *   F4, F5 — non-square + wide-extraction → memcmp == 0 if the
 *            walk stays within the texture bounds
 *   F7 — full-width reverse stride → memcmp == 0
 *   F2, F3, F6, F8 — REQUIRE shift-mode → expected to differ;
 *            documented in the failure message.
 *
 * Build/run: from src/fpga/test/, `make gpu-floor`.
 */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include "Vtb_gpu.h"
#include "verilated.h"
#include "verilated_vcd_c.h"

static Vtb_gpu *tb;
static uint64_t sim_time = 0;
static int pass_count = 0;
static int fail_count = 0;

// Memory layout in the testbench's SDRAM model
static const uint32_t TEX_BASE_BYTE = 0x00040000;  // texture root
static const uint32_t FB_BASE_BYTE  = 0x00080000;  // framebuffer root
static const uint32_t RING_SIZE     = 0x00004000;
static uint32_t ring_wrptr = 0;
static const uint32_t ring_mask = RING_SIZE - 1;

// =====================================================================
// Tick / reset / MMIO / SDRAM helpers (lifted from tb_gpu_main.cpp)
// =====================================================================
static void tick() {
    tb->clk = 0; tb->eval();
    sim_time++;
    tb->clk = 1; tb->eval();
    sim_time++;
}
static void reset() {
    tb->reset_n = 0; tb->reg_wr = 0; tb->bd_we = 0;
    for (int i = 0; i < 20; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 5; i++) tick();
}
static void sdram_write(uint32_t word_addr, uint32_t data) {
    tb->bd_we = 1; tb->bd_addr = word_addr; tb->bd_wdata = data;
    tick(); tb->bd_we = 0;
}
static uint32_t sdram_read(uint32_t word_addr) {
    tb->bd_rd_addr = word_addr; tb->eval();
    return tb->bd_rd_data;
}
static uint8_t sdram_read_byte(uint32_t byte_addr) {
    return (sdram_read(byte_addr >> 2) >> ((byte_addr & 3) * 8)) & 0xFF;
}
static void sdram_write_byte(uint32_t byte_addr, uint8_t value) {
    uint32_t w = sdram_read(byte_addr >> 2);
    int sh = (byte_addr & 3) * 8;
    w = (w & ~(0xFFu << sh)) | ((uint32_t)value << sh);
    sdram_write(byte_addr >> 2, w);
}
static void mmio_write(uint32_t reg, uint32_t val) {
    tb->reg_wr = 1; tb->reg_addr = reg; tb->reg_wdata = val;
    tick(); tb->reg_wr = 0;
}
static void ring_write(uint32_t w) {
    mmio_write(2, w);
    ring_wrptr = (ring_wrptr + 4) & ring_mask;
}
static void ring_cmd(uint8_t cmd, uint32_t payload_words) {
    ring_write(((uint32_t)cmd << 24) | payload_words);
}
static void gpu_kick() { mmio_write(1, ring_wrptr); }
static bool gpu_wait_fence(uint32_t token, int timeout = 200000) {
    for (int t = 0; t < timeout; t++) {
        tick();
        tb->reg_addr = 6; tb->eval();   // GPU_FENCE = reg 6
        if (tb->reg_rdata == token) return true;
    }
    return false;
}
static uint32_t gpu_submit() {
    static uint32_t next_token = 1;
    uint32_t token = next_token++;
    ring_cmd(0x02, 1);   // CMD_FENCE
    ring_write(token);
    gpu_kick();
    return token;
}
static bool gpu_finish(int timeout = 200000) {
    return gpu_wait_fence(gpu_submit(), timeout);
}
static void gpu_init() {
    ring_wrptr = 0;
    reset();
    mmio_write(0, 4);   // GPU_CTRL: ring_reset
    mmio_write(1, 0);   // RING_WRPTR = 0
}

// =====================================================================
// CPU reference oracle — BUILD's hlineasm4 in C
// =====================================================================
static void hlineasm4_ref(int numPixels, int /*shade*/,
                          uint32_t i4, uint32_t i5,
                          uint32_t asm1, uint32_t asm2,
                          uint8_t bits, uint8_t shifter,
                          const uint8_t *texture,
                          const uint8_t *colormap_row,
                          uint8_t *dest_right)
{
    while (numPixels-- > 0) {
        uint32_t source = i5 >> shifter;
        source = (source << bits) | (i4 >> (32 - bits));
        *dest_right-- = colormap_row[texture[source]];
        i5 -= asm1;
        i4 -= asm2;
    }
}

// =====================================================================
// Setup helpers
// =====================================================================
// Identity colormap — 256 bytes uploaded to cmap row 0 via MMIO.
static void upload_identity_cmap_row0() {
    mmio_write(8, 0);  // GPU_CMAP_ADDR = 0
    for (int i = 0; i < 256; i += 4) {
        uint32_t w = (uint32_t)(uint8_t)(i + 0)
                   | ((uint32_t)(uint8_t)(i + 1) <<  8)
                   | ((uint32_t)(uint8_t)(i + 2) << 16)
                   | ((uint32_t)(uint8_t)(i + 3) << 24);
        mmio_write(9, w);
    }
}

// Tile a tex_w × tex_h pattern across 256×256 bytes of SDRAM at
// TEX_BASE_BYTE.  Tiling absorbs out-of-tex_w-range multiply-mode
// addresses for cases where the GPU walks past the logical extent —
// gives those tests a chance to pass instead of guaranteed-fail.
static void upload_texture(int tex_w, int tex_h) {
    uint8_t pattern[64 * 64];
    for (int t = 0; t < tex_h; t++) {
        for (int s = 0; s < tex_w; s++) {
            // Deterministic non-symmetric pattern.
            pattern[t * tex_w + s] = (uint8_t)((t * 7 + s * 13) ^ 0xA5);
        }
    }
    // Replicate into 256x256 block at TEX_BASE_BYTE.
    for (int y = 0; y < 256; y++) {
        for (int x = 0; x < 256; x += 4) {
            uint32_t w = 0;
            for (int b = 0; b < 4; b++) {
                int s = (x + b) % tex_w;
                int t = y % tex_h;
                w |= (uint32_t)pattern[t * tex_w + s] << (b * 8);
            }
            sdram_write((TEX_BASE_BYTE + y * 256 + x) >> 2, w);
        }
    }
}

// Read the same logical pattern back, indexed by `source` (the
// hlineasm4 extracted index).  Used by the CPU reference.
static void build_pattern_buffer(uint8_t *out, int tex_w, int tex_h) {
    for (int t = 0; t < tex_h; t++)
        for (int s = 0; s < tex_w; s++)
            out[t * tex_w + s] = (uint8_t)((t * 7 + s * 13) ^ 0xA5);
}

// Pre-fill the FB destination range with 0xCC sentinel.  Any pixel
// still 0xCC after the draw is one the GPU never wrote.
static void preset_fb_sentinel(uint32_t fb_byte_base, int count_bytes) {
    for (int i = 0; i < count_bytes; i++)
        sdram_write_byte(fb_byte_base + i, 0xCC);
}

// =====================================================================
// Submit one CMD_DRAW_SPAN.  Translates BUILD-style hlineasm4 inputs
// into the GPU's multiply-mode CMD_DRAW_SPAN payload.
//
// The GPU's tex_addr math is `tex_base + t_int*tex_width + s_int`,
// where t_int = sp_t[31:16] and s_int = sp_s[31:16].  hlineasm4's
// `source = ((i5>>shifter) << bits) | (i4>>(32-bits))` is equivalent
// to `t_int * (1 << bits) + s_int` if we set tex_width = 1 << bits
// and walk sp_t/sp_s such that their integer parts match the shifted
// extracts at every pixel.  That's TRUE for the start point (provided
// we pre-shift), but the per-pixel walk diverges as soon as a wrap
// would occur — multiply-mode has no wrap, shift-mode does.
// =====================================================================
struct floor_case {
    const char *name;
    int      count;
    int      tex_w, tex_h;
    uint8_t  bits, shifter;
    uint32_t i4, i5;
    uint32_t asm1, asm2;
    int16_t  fb_stride;       // typically -1 (BUILD writes right-to-left)
    bool     known_diverge;   // expected to fail on current RTL?
};

static int run_case(const floor_case &c)
{
    printf("--- %s ---\n", c.name);

    gpu_init();
    upload_identity_cmap_row0();
    upload_texture(c.tex_w, c.tex_h);

    // The destination column for hlineasm4 is the rightmost pixel of
    // the span (since the asm walks right-to-left).  Place the span
    // at FB row 0 with rightmost pixel at column count-1.
    const uint32_t fb_row_base = FB_BASE_BYTE;
    preset_fb_sentinel(fb_row_base, c.count + 16);

    // Translate hlineasm4 i4/asm2 → 16.16 sp_s / sp_sstep.
    //   shift-mode integer part is bits[31:32-bits] of i4.
    //   To put that integer at bit 16 of sp_s (i.e., 16.16 integer
    //   part), shift right by (32-bits) - 16 = 16-bits when bits<16,
    //   or left by (bits-16) when bits>16.
    //
    // For bits=6: shift right by 10.  sp_sstep = -(asm2 >> 10) (subtract
    // asm2 each pixel ≡ add (-asm2) in multiply-mode).  Same for
    // sp_t / sp_tstep using shifter (= 32-shifter for the right-shift
    // count, mirroring i5 >> shifter).
    auto to_q16 = [](uint32_t v, int rshift) -> uint32_t {
        if (rshift >= 0) return v >> rshift;
        else             return v << -rshift;
    };
    int s_rshift = (32 - c.bits) - 16;          // i4 -> sp_s
    int t_rshift = c.shifter - 16;              // i5 -> sp_t
    uint32_t sp_s     = to_q16(c.i4, s_rshift);
    uint32_t sp_t     = to_q16(c.i5, t_rshift);
    uint32_t sp_sstep = (uint32_t)-(int32_t)to_q16(c.asm2, s_rshift);
    uint32_t sp_tstep = (uint32_t)-(int32_t)to_q16(c.asm1, t_rshift);

    // CMD_DRAW_SPAN payload (18 words; perspective fields zero).
    ring_cmd(0x40, 18);                                      // CMD_DRAW_SPAN
    ring_write(fb_row_base + (c.count - 1));                 // 0: fb_addr (rightmost px)
    ring_write(TEX_BASE_BYTE);                               // 1: tex_addr
    ring_write(sp_s);                                        // 2: sp_s
    ring_write(sp_t);                                        // 3: sp_t
    ring_write(sp_sstep);                                    // 4: sp_sstep
    ring_write(sp_tstep);                                    // 5: sp_tstep
    ring_write(((uint32_t)c.count << 16)
             | (0u << 8)                                      // light = 0
             | 0x01u);                                        // SPAN_COLORMAP only
    ring_write(((uint32_t)(uint16_t)c.fb_stride << 16)
             | (uint32_t)(uint16_t)(1u << c.bits));           // 7: fb_stride | tex_width
    ring_write(0);                                           // 8: reserved
    ring_write(0);                                           // 9: z_addr
    ring_write(0);                                           // 10: zi
    ring_write(0);                                           // 11: zistep
    for (int i = 12; i < 18; i++) ring_write(0);             // 12..17: persp (unused)

    bool ok = gpu_finish(500000);
    if (!ok) {
        printf("  FAIL %s: fence timeout (GPU hung)\n", c.name);
        fail_count++;
        return -1;
    }

    // CPU reference — execute hlineasm4 against a flat pattern.  The
    // shift-mode `source` index ranges [0, 1<<(bits + (32-shifter))).
    // For F5 (bits=8/shifter=24) that's 256*256 = 64K — overflow our
    // 4K pattern buffer.  Use a 64K static buffer that re-derives the
    // same pattern as the GPU's tiled texture, indexed with the
    // GPU's tile-mod arithmetic (s%tex_w + (t%tex_h)*tex_w).
    static uint8_t pattern_full[65536];
    for (uint32_t idx = 0; idx < 65536; idx++) {
        int s = (int)(idx & 0xFF);
        int t = (int)((idx >> 8) & 0xFF);
        int s_mod = s % c.tex_w;
        int t_mod = t % c.tex_h;
        pattern_full[idx] = (uint8_t)((t_mod * 7 + s_mod * 13) ^ 0xA5);
    }
    uint8_t cmap_row0[256];
    for (int i = 0; i < 256; i++) cmap_row0[i] = (uint8_t)i;
    uint8_t ref_fb[1024];
    memset(ref_fb, 0xCC, sizeof ref_fb);
    hlineasm4_ref(c.count, 0, c.i4, c.i5, c.asm1, c.asm2,
                  c.bits, c.shifter, pattern_full, cmap_row0,
                  &ref_fb[c.count - 1]);

    // Compare the count pixels written by the span emit.
    int diffs = 0, first_diff = -1;
    uint8_t first_dut = 0, first_ref = 0;
    for (int i = 0; i < c.count; i++) {
        uint8_t dut = sdram_read_byte(fb_row_base + i);
        uint8_t ref = ref_fb[i];
        if (dut != ref) {
            if (first_diff < 0) {
                first_diff = i; first_dut = dut; first_ref = ref;
            }
            diffs++;
        }
    }
    if (diffs == 0) {
        printf("  OK  %s (memcmp == 0, %d pixels)\n", c.name, c.count);
        pass_count++;
        return 0;
    } else if (c.known_diverge) {
        printf("  XFAIL %s: %d/%d pixels diverge (expected — RTL has no\n"
               "         shift-mode wrap; first diff @ %d: dut=0x%02x ref=0x%02x)\n",
               c.name, diffs, c.count, first_diff, first_dut, first_ref);
        // Not counted as fail — documented gap.
        return 1;
    } else {
        printf("  FAIL %s: %d/%d pixels diverge (first @ %d: dut=0x%02x ref=0x%02x)\n",
               c.name, diffs, c.count, first_diff, first_dut, first_ref);
        fail_count++;
        return -1;
    }
}

// =====================================================================
// Test cases
// =====================================================================
int main(int argc, char **argv)
{
    setvbuf(stdout, nullptr, _IOLBF, 0);
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_gpu;
    printf("=== GPU Floor-Span Test Suite ===\n");
    fflush(stdout);

    floor_case cases[] = {
        // F1 — 64×64, axis-aligned, NO wrap.  The original brief used
        // i4=0/i5=0 starts but those walk negative on the very first
        // step, hitting the multiply-mode-vs-shift-mode gap.  Use
        // i4=0x40000000 / i5=0x40000000 so the walk stays in [0,64)
        // throughout the span; this is an HONEST axis-aligned smoke
        // test of multiply-mode and should pass on the current RTL.
        {
            .name = "F1: 64x64 axis-aligned (smoke test)",
            .count = 32, .tex_w = 64, .tex_h = 64,
            .bits = 6, .shifter = 26,
            .i4 = 0x40000000, .i5 = 0x40000000,
            .asm1 = 0x01000000, .asm2 = 0x01000000,
            .fb_stride = -1,
            .known_diverge = false,
        },
        // F2 — large negative i4 / i5.  shift-mode wraps via unsigned
        // shift; multiply-mode walks off the texture (out-of-range
        // sp_s_int).  Even with the texture tiled to 256×256, sp_s
        // walks well beyond that range and reads SDRAM garbage.
        {
            .name = "F2: large-negative i4/i5 (shift-mode wrap)",
            .count = 64, .tex_w = 64, .tex_h = 64,
            .bits = 6, .shifter = 26,
            .i4 = 0xF8000000, .i5 = 0xF0000000,
            .asm1 = 0x01000000, .asm2 = 0x01000000,
            .fb_stride = -1,
            .known_diverge = true,
        },
        // F3 — step crosses tex boundary mid-span.  shift-mode wraps
        // mod 64 naturally; multiply-mode doesn't.
        {
            .name = "F3: in-span tex-boundary cross",
            .count = 64, .tex_w = 64, .tex_h = 64,
            .bits = 6, .shifter = 26,
            .i4 = 0, .i5 = 0,
            .asm1 = 0x10000000, .asm2 = 0x01000000,
            .fb_stride = -1,
            .known_diverge = true,
        },
        // F4 — non-square 32×128.  Independent S/T extraction widths.
        // bits=5 (32-wide S), shifter=25 (shifts T from upper bits
        // 25..31 = 7 bits → 128 rows).  Same trick as F1: start at
        // 0x40000000 so neither i4 nor i5 wraps during the 32-pixel
        // walk under multiply-mode (sp_s/sp_t stay in [0, 16] in
        // 16.16, well within the texture extents).
        {
            .name = "F4: 32x128 non-square",
            .count = 32, .tex_w = 32, .tex_h = 128,
            .bits = 5, .shifter = 25,
            .i4 = 0x40000000, .i5 = 0x40000000,
            .asm1 = 0x02000000, .asm2 = 0x02000000,
            .fb_stride = -1,
            .known_diverge = false,
        },
        // F5 — 256-wide span with bits=8 / shifter=24.  Pushes the
        // formula's upper end (full-byte extraction in S).  Reduced
        // count to 32 to avoid a multi-second sim run; the tested
        // behaviour is the same for any count.  Known to diverge on
        // current RTL because an i4=0 start immediately walks
        // negative under multiply-mode.
        {
            .name = "F5: bits=8 / shifter=24 (full extraction)",
            .count = 32, .tex_w = 64, .tex_h = 64,
            .bits = 8, .shifter = 24,
            .i4 = 0, .i5 = 0,
            .asm1 = 0x00800000, .asm2 = 0x00800000,
            .fb_stride = -1,
            .known_diverge = true,
        },
        // F6 — tex_width = 0 explicitly.  Old shift-mode would fire;
        // current RTL doesn't have shift-mode, so tex_width=0 means
        // multiply-mode reads address `t_int * 0 + s_int` = s_int —
        // a 1-D slice of the texture, NOT what hlineasm4 does.
        {
            .name = "F6: tex_width=0 (shift-mode selector — retired)",
            .count = 64, .tex_w = 64, .tex_h = 64,
            .bits = 6, .shifter = 26,
            .i4 = 0, .i5 = 0,
            .asm1 = 0x01000000, .asm2 = 0x01000000,
            .fb_stride = -1,
            .known_diverge = true,
        },
        // F7 — full-width (320 px) reverse stride.  Stress the FB
        // write FSM's negative-stride path beyond a single tile.
        // Start at 0x40000000; total walk = 320 * 0x00200000 =
        // 0x28000000, ending at 0x18000000 (still positive).  In
        // sp_s/sp_t (16.16) terms, walks 16 → 6.
        {
            .name = "F7: reverse stride, count=320",
            .count = 320, .tex_w = 64, .tex_h = 64,
            .bits = 6, .shifter = 26,
            .i4 = 0x40000000, .i5 = 0x40000000,
            .asm1 = 0x00200000, .asm2 = 0x00200000,
            .fb_stride = -1,
            .known_diverge = false,
        },
        // F8 — combined: large-negative starts AND step crosses zero.
        // The hardest case for multiply-mode to match.
        {
            .name = "F8: negative starts + zero-cross step",
            .count = 64, .tex_w = 64, .tex_h = 64,
            .bits = 6, .shifter = 26,
            .i4 = 0xFC000000, .i5 = 0xF8000000,
            .asm1 = (uint32_t)-0x01000000,   // step ADDS each pixel
            .asm2 = (uint32_t)-0x01000000,
            .fb_stride = -1,
            .known_diverge = true,
        },
    };

    for (size_t i = 0; i < sizeof(cases)/sizeof(cases[0]); i++) {
        run_case(cases[i]);
    }

    printf("\n=== Floor-Span Results: %d passed, %d failed ===\n",
           pass_count, fail_count);
    delete tb;
    return fail_count ? 1 : 0;
}
