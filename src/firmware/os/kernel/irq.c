/*
 * openfpgaOS IRQ Dispatcher
 * Central interrupt handler — dispatches timer and external IRQs
 * to registered callbacks.
 */

#include "irq.h"
#include "../hal/regs.h"

/* Timer ISR callback — lives in syscall.c (handles timer_callback + sigalrm) */
extern void timer_isr_callback(void);

/* Terminal printf — declared here to avoid dragging all of terminal.h. */
extern void of_term_printf(const char *fmt, ...);

/* HW mixer probe — runs from the timer ISR at ~1 Hz (1000 ticks at 1 kHz
 * timer rate).  Prints MIX_STATUS (active voice count) and MIX_IRQ_PENDING
 * (voice-end bitmap), W1C-ing the pending bits so we observe new events
 * per window rather than accumulated ones.  If voices are dying every
 * ~2 ms the irq bitmap will light up with those voice indices each
 * second — pinpoints pos/LEN corruption vs. vol corruption. */
static uint32_t mixer_probe_tick_count;
static uint32_t mixer_probe_last_irq;
static uint32_t mixer_probe_last_act;
static uint32_t mixer_probe_prev_samples;   /* last second's sample counter */
static uint32_t mixer_probe_prev_plays;     /* last second's play counter */
volatile uint32_t play_counter_diag;        /* bumped by mixer.c play_internal */

/* IRQ callbacks */
static void (*external_cb)(uint32_t source);
static void (*vsync_cb)(void);
static void (*link_rx_cb)(uint32_t word);

void of_irq_init(void) {
    external_cb = 0;
    vsync_cb = 0;
    link_rx_cb = 0;
}

void of_irq_register_external(void (*cb)(uint32_t source)) {
    external_cb = cb;
}

void of_irq_register_vsync(void (*cb)(void)) {
    vsync_cb = cb;
    if (cb)
        IRQ_MASK |= IRQ_MASK_VSYNC;
    else
        IRQ_MASK &= ~IRQ_MASK_VSYNC;
}

/* Voice-end IRQ retired with the hardware mixer.  Ended voices are now
 * reported synchronously via of_mixer_poll_ended(); the registration
 * call is preserved for ABI but accepts and drops the callback. */
void of_irq_register_mixer_end(void (*cb)(uint32_t ended_mask)) {
    (void)cb;
}

void of_irq_register_link_rx(void (*cb)(uint32_t word)) {
    link_rx_cb = cb;
    if (cb)
        IRQ_MASK |= IRQ_MASK_LINK;
    else
        IRQ_MASK &= ~IRQ_MASK_LINK;
}

/*
 * irq_handler — called from assembly trap entry for all hardware interrupts.
 * Dispatches based on mcause code: 7 = machine timer, 11 = machine external.
 */
void irq_handler(void *frame) {
    uint32_t *f = (uint32_t *)frame;
    uint32_t mcause = f[33];  /* offset 132 / 4 */
    uint32_t code = mcause & 0x7FFFFFFF;

    if (code == 7) {
        /* Machine timer interrupt — clear pending and run the app
         * callback.  The HW mixer runs autonomously off audio_output's
         * dcfifo, so no audio work happens here. */
        TIMER_CTRL = TIMER_CTRL_ENABLE | TIMER_CTRL_W1C_IRQ;

        /* Re-enabled: main-thread callers now guard SEL+field
         * sequences with mstatus.MIE save/restore (mixer.c), so the
         * ISR's probe SEL writes can't corrupt a main-thread sequence.
         * Within the ISR, probe + smp_voice_tick run sequentially.
         *
         * Threshold 50: of_midi_pump registers the timer at 50 Hz (1 kHz
         * smp_voice_tick rate is virtual, driven inside the pump by
         * iterating), so 50 ticks ≈ 1 s wall-clock.  The old `1000`
         * threshold assumed a 1 kHz timer and only fired every 20 s. */
        if (++mixer_probe_tick_count >= 50) {
            mixer_probe_tick_count = 0;
            static uint32_t sec_counter;
            sec_counter++;

            uint32_t afifo   = AUDIO_STATUS & 0x7FF;
            uint32_t scnt    = MIX_SAMPLE_COUNT;
            uint32_t srate   = scnt - mixer_probe_prev_samples;
            uint32_t last    = MIX_LAST_SAMPLE;
            uint32_t actv    = MIX_STATUS & 0x3Fu;  /* HW-shadow active voice popcount */
            uint32_t amask   = MIX_ACTIVE_MASK;     /* 32-bit voice_active bitmap */
            mixer_probe_prev_samples = scnt;

            /* Probe a few voices' pos_int to see if positions are
             * actually advancing.  If all voices report the same pos
             * across seconds → voices frozen → mixer producing near-DC
             * output (matches the 04Exxxxx clustering we saw). */
            MIX_VOICE_SEL = 0;  uint32_t p0  = MIX_VOICE_POS & 0x3FFFFF;
            MIX_VOICE_SEL = 1;  uint32_t p1  = MIX_VOICE_POS & 0x3FFFFF;
            MIX_VOICE_SEL = 2;  uint32_t p2  = MIX_VOICE_POS & 0x3FFFFF;
            MIX_VOICE_SEL = 5;  uint32_t p5  = MIX_VOICE_POS & 0x3FFFFF;

            uint32_t pc = play_counter_diag;
            uint32_t plays_per_sec = pc - mixer_probe_prev_plays;
            mixer_probe_prev_plays = pc;

            of_term_printf("[mix s=%u act=%u mask=%08x fifo=%u rate=%u last=%08x p0=%u p1=%u p2=%u p5=%u plays/s=%u]\n",
                           (unsigned)sec_counter, (unsigned)actv, (unsigned)amask,
                           (unsigned)afifo, (unsigned)srate,
                           (unsigned)last,
                           (unsigned)p0, (unsigned)p1, (unsigned)p2, (unsigned)p5,
                           (unsigned)plays_per_sec);

            mixer_probe_last_irq = 0;
            mixer_probe_last_act = 0;
        }

        timer_isr_callback();
    }

    if (code == 11) {
        /* Machine external interrupt — UART RX, link, vsync. */
        uint32_t source = 0;

        if (UART_STATUS & UART_RX_AVAIL)
            source |= IRQ_SRC_UART_RX;

        /* Link: rx_ready flag (cleared by reading RX_DATA) */
        if (LINK_STATUS & LINK_STATUS_RX_READY)
            source |= IRQ_SRC_LINK_RX;

        /* Vsync: check pending, W1C clear */
        if (VSYNC_IRQ_PENDING) {
            VSYNC_IRQ_PENDING = 1;  /* W1C */
            source |= IRQ_SRC_VSYNC;
        }

        /* Dispatch: dedicated callbacks */
        if ((source & IRQ_SRC_LINK_RX) && link_rx_cb) {
            uint32_t word = LINK_RX_DATA;  /* read clears rx_ready + IRQ */
            link_rx_cb(word);
        }

        if ((source & IRQ_SRC_VSYNC) && vsync_cb)
            vsync_cb();

        if (external_cb)
            external_cb(source);
    }
}
