/*
 * Memory benchmark for Verilator system simulation
 *
 * Measures memcpy throughput for various transfer sizes.
 * Outputs results via UART (0x4F000004).
 * Signals completion via BRAM result register (0x3FF00).
 *
 * Memory map:
 *   0x00000000 - BRAM (code + stack)
 *   0x10000000 - SDRAM (test data)
 */

#include <stdint.h>

#define UART_STATUS (*(volatile uint32_t *)0x4F000000)
#define UART_TX     (*(volatile uint32_t *)0x4F000004)
#define RESULT_STATUS (*(volatile uint32_t *)0x0003FF00)
#define RESULT_VALUE  (*(volatile uint32_t *)0x0003FF04)

static void putchar_uart(char c) {
    while (!(UART_STATUS & 0x2))  // Wait for TX ready
        ;
    UART_TX = c;
}

static void puts_uart(const char *s) {
    while (*s) putchar_uart(*s++);
}

static void put_hex(uint32_t v) {
    for (int i = 28; i >= 0; i -= 4) {
        int nib = (v >> i) & 0xF;
        putchar_uart(nib < 10 ? '0' + nib : 'A' + nib - 10);
    }
}

static void put_dec(uint32_t v) {
    char buf[12];
    int i = 0;
    if (v == 0) { putchar_uart('0'); return; }
    while (v > 0) { buf[i++] = '0' + (v % 10); v /= 10; }
    while (i > 0) putchar_uart(buf[--i]);
}

static inline uint32_t rdcycle(void) {
    uint32_t c;
    __asm__ volatile("rdcycle %0" : "=r"(c));
    return c;
}

// Simple word-aligned memcpy for benchmarking
static void *bench_memcpy(void *dst, const void *src, uint32_t n) {
    uint32_t *d = (uint32_t *)dst;
    const uint32_t *s = (const uint32_t *)src;
    n /= 4;
    while (n >= 8) {
        uint32_t a = s[0], b = s[1];
        d[0] = a; d[1] = b;
        a = s[2]; b = s[3];
        d[2] = a; d[3] = b;
        a = s[4]; b = s[5];
        d[4] = a; d[5] = b;
        a = s[6]; b = s[7];
        d[6] = a; d[7] = b;
        s += 8; d += 8; n -= 8;
    }
    while (n--) *d++ = *s++;
    return dst;
}

static void run_bench(uint32_t size) {
    volatile uint32_t *src = (volatile uint32_t *)0x10000000;
    volatile uint32_t *dst = (volatile uint32_t *)0x10100000;

    // Initialize source
    for (uint32_t i = 0; i < size / 4; i++)
        src[i] = i;

    // Fence to flush caches
    __asm__ volatile("fence");

    // Timed copy
    uint32_t t0 = rdcycle();
    bench_memcpy((void *)dst, (void *)src, size);
    __asm__ volatile("fence");
    uint32_t t1 = rdcycle();

    uint32_t cycles = t1 - t0;

    // Verify first and last word
    uint32_t ok = (dst[0] == 0) && (dst[size / 4 - 1] == size / 4 - 1);

    // Print result
    puts_uart("  ");
    put_dec(size);
    puts_uart(" bytes: ");
    put_dec(cycles);
    puts_uart(" cycles");
    if (!ok) puts_uart(" VERIFY FAIL");
    putchar_uart('\n');
}

void _start(void) __attribute__((section(".text.init")));

void _start(void) {
    // Set stack pointer to top of BRAM
    __asm__ volatile("li sp, 0x0003F000");

    puts_uart("=== Memory Benchmark ===\n");

    // Small in-cache copies
    puts_uart("In-cache:\n");
    run_bench(256);
    run_bench(1024);
    run_bench(4096);
    run_bench(16384);

    // Large out-of-cache copies
    puts_uart("Out-of-cache:\n");
    run_bench(65536);
    run_bench(131072);

    puts_uart("Done\n");

    // Signal completion
    RESULT_STATUS = 1;

    // Halt
    while (1) __asm__ volatile("wfi");
}
