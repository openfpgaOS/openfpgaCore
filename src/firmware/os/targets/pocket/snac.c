//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS SNAC Controller Driver
 * Software protocol drivers using the hardware shift register.
 *
 * Supported protocols:
 *   Config A: NES, SNES, DB15 (shift-register based)
 *   Config B: PSX digital + analog (SPI-based)
 *   PC Engine: GPIO bit-bang (no shift register needed)
 */

#include "snac.h"
#include "regs.h"
#include "../../hal/input.h"  /* OF_BTN_* canonical layout */

static snac_controller_t snac_state[2];
static uint8_t active_type;
static int     snac_active_flag;
static int     snac_hw_active_flag;
static int     snac_poll_busy;
static uint8_t snac_gpio_out_shadow;
static uint8_t psx_att_pin;
static uint8_t psx_att_locked;
static uint8_t psx_invalid_poll_count;
static uint64_t snac_last_poll_cycles;

#define PSX_ATT_SETUP_DELAY      1000
#define PSX_INTERBYTE_DELAY       256
#define PSX_ACK_TIMEOUT           128
#define PSX_ACK_RELEASE_TIMEOUT   256
#define PSX_CLEAR_AFTER_INVALID_POLLS     8

static void snac_gpio_set(uint8_t out, uint8_t dir) {
    snac_gpio_out_shadow = out;
    snac_gpio_write(out, dir);
}

static void snac_delay(volatile int cycles) {
    while (cycles-- > 0)
        ;
}

static int snac_type_is_psx(uint8_t snac_type) {
    return snac_type == SNAC_PSX ||
           snac_type == SNAC_PSX_FAST;
}

static void snac_commit_state(int player, const snac_controller_t *next) {
    if (player < 0 || player > 1)
        return;

    uint16_t old_buttons = snac_state[player].buttons;
    uint16_t new_buttons = next->buttons;

    snac_controller_t filtered = *next;
    filtered.buttons_pressed = snac_state[player].buttons_pressed |
                               (uint16_t)(new_buttons & (uint16_t)~old_buttons);
    filtered.buttons_released = snac_state[player].buttons_released |
                                (uint16_t)((uint16_t)~new_buttons & old_buttons);
    snac_state[player] = filtered;
}

static void snac_commit_empty(int player) {
    snac_controller_t next = {0};
    snac_commit_state(player, &next);
}

static int16_t psx_axis_to_input(uint8_t value) {
    return (int16_t)(((int16_t)value - 128) * 256);
}

/* ============================================================
 * Protocol: NES / SNES / DB15 (Config A)
 * Shift-register protocol: LATCH pulse, then clock out N bits.
 * CLK = OUT1 (pin[0]), LATCH = OUT2 (pin[1]), DATA = IO3 (pin[2])
 * ============================================================ */

static void poll_serlatch(int bits) {
    /* Single transfer: latch + shift `bits` bits from DATA line.
     * TX data is don't-care (controller doesn't receive). */
    uint32_t rx = snac_xfer(0, bits, 1);

    /* NES/SNES data is active-low, MSB received first.
     * Shift the received bits to proper position and invert. */
    uint16_t raw = ~((uint16_t)(rx >> (32 - bits))) & ((1 << bits) - 1);

    /* Map to Pocket button format.
     * NES (8 bits):  A B Select Start Up Down Left Right
     * SNES (16 bits): B Y Select Start Up Down Left Right A X L R (4 unused)
     * DB15 (active-high direct mapping, no standard — pass raw) */
    uint16_t buttons = 0;

    if (active_type == SNAC_NES) {
        if (raw & 0x80) buttons |= OF_BTN_A;
        if (raw & 0x40) buttons |= OF_BTN_B;
        if (raw & 0x20) buttons |= OF_BTN_SELECT;
        if (raw & 0x10) buttons |= OF_BTN_START;
        if (raw & 0x08) buttons |= OF_BTN_UP;
        if (raw & 0x04) buttons |= OF_BTN_DOWN;
        if (raw & 0x02) buttons |= OF_BTN_LEFT;
        if (raw & 0x01) buttons |= OF_BTN_RIGHT;
    } else if (active_type == SNAC_SNES || active_type == SNAC_SNES_SWAP) {
        if (raw & 0x8000) buttons |= OF_BTN_B;
        if (raw & 0x4000) buttons |= OF_BTN_Y;
        if (raw & 0x2000) buttons |= OF_BTN_SELECT;
        if (raw & 0x1000) buttons |= OF_BTN_START;
        if (raw & 0x0800) buttons |= OF_BTN_UP;
        if (raw & 0x0400) buttons |= OF_BTN_DOWN;
        if (raw & 0x0200) buttons |= OF_BTN_LEFT;
        if (raw & 0x0100) buttons |= OF_BTN_RIGHT;
        if (raw & 0x0080) buttons |= OF_BTN_A;
        if (raw & 0x0040) buttons |= OF_BTN_X;
        if (raw & 0x0020) buttons |= OF_BTN_L1;
        if (raw & 0x0010) buttons |= OF_BTN_R1;

        if (active_type == SNAC_SNES_SWAP) {
            /* Swap A/B and X/Y */
            uint16_t a = buttons & OF_BTN_A;
            uint16_t b = buttons & OF_BTN_B;
            uint16_t x = buttons & OF_BTN_X;
            uint16_t y = buttons & OF_BTN_Y;
            buttons &= ~(OF_BTN_A | OF_BTN_B | OF_BTN_X | OF_BTN_Y);
            if (a) buttons |= OF_BTN_B;
            if (b) buttons |= OF_BTN_A;
            if (x) buttons |= OF_BTN_Y;
            if (y) buttons |= OF_BTN_X;
        }
    } else {
        /* DB15: direct mapping (active-high, 12 bits) */
        buttons = raw;
    }

    snac_controller_t next = {
        .buttons = buttons,
        .joy_lx = 0,
        .joy_ly = 0,
        .joy_rx = 0,
        .joy_ry = 0,
    };
    snac_commit_state(0, &next);
}

/* ============================================================
 * Protocol: PlayStation (Config B)
 * SPI-based: CLK = pin30 (pin[6]), CMD = IO6 (pin[7]), DAT = IN4 (pin[5])
 * ATT = OUT1 (pin[0])
 * ============================================================ */

/* PSX SPI byte transfer: send cmd, receive response */
static uint8_t psx_xfer_byte(uint8_t cmd) {
    /* PSX sends LSB first, but our shifter is MSB first.
     * Bit-reverse the command byte and result. */
    uint8_t tx_rev = 0;
    for (int i = 0; i < 8; i++)
        if (cmd & (1 << i)) tx_rev |= (0x80 >> i);

    uint32_t rx = snac_xfer((uint32_t)tx_rev << 24, 8, 0);
    uint8_t rx_byte = (uint8_t)(rx >> 24);

    /* Bit-reverse received byte */
    uint8_t rx_rev = 0;
    for (int i = 0; i < 8; i++)
        if (rx_byte & (1 << i)) rx_rev |= (0x80 >> i);

    return rx_rev;
}

static void psx_wait_ack_or_gap(void) {
    int saw_ack = 0;

    /* ACK is optional for our poll path: some adapters do not expose it on
     * the expected pin. Keep this as a short probe, then use a fixed gap so
     * SNAC polling cannot become a frame-time sink. */
    for (volatile int i = 0; i < PSX_ACK_TIMEOUT; i++) {
        if ((snac_gpio_read() & SNAC_PIN_BK06) == 0) {
            saw_ack = 1;
            break;
        }
    }

    if (saw_ack) {
        for (volatile int i = 0; i < PSX_ACK_RELEASE_TIMEOUT; i++) {
            if (snac_gpio_read() & SNAC_PIN_BK06)
                break;
        }
    } else {
        snac_delay(PSX_INTERBYTE_DELAY);
    }
}

static uint8_t psx_xfer_byte_gap(uint8_t cmd) {
    uint8_t rx = psx_xfer_byte(cmd);
    psx_wait_ack_or_gap();
    return rx;
}

static int psx_id_valid(uint8_t id) {
    return id == 0x41 ||   /* digital */
           id == 0x53 ||   /* analog green */
           id == 0x73;     /* analog red / DualShock */
}

static int psx_response_valid(uint8_t id, uint8_t status) {
    /* Some Analogizer/SNAC PSX adapters do not report the 0x5A status byte
     * perfectly every poll, but the device ID remains stable.  Treat a known
     * ID as a synchronized frame so releases do not wait behind transient
     * status-byte misses. */
    (void)status;
    return psx_id_valid(id);
}

static int poll_psx_att(int analog, uint8_t att_pin, int commit) {
    uint8_t dir = ((SNAC_DIR_IO5 | SNAC_DIR_IO6) >> 8) | 0x03;
    uint8_t idle_out = snac_gpio_out_shadow |
                       SNAC_PIN_OUT1 |
                       SNAC_PIN_OUT2 |
                       SNAC_PIN_IO5 |
                       SNAC_PIN_IO6;

    /* Assert ATT/select (active low).  Analogizer Config B adapters may
     * route the active select to OUT1 or OUT2, so the caller probes both. */
    snac_gpio_set(idle_out & (uint8_t)~att_pin, dir);

    /* Give the controller time to see ATT before the first clock edge. */
    snac_delay(PSX_ATT_SETUP_DELAY);

    /* Address: 0x01 */
    psx_xfer_byte_gap(0x01);
    /* Command: 0x42 (Read Data), get device ID */
    uint8_t id = psx_xfer_byte_gap(0x42);
    /* Get status byte (usually 0x5A = ready) */
    uint8_t status = psx_xfer_byte_gap(0x00);
    /* Button bytes (active low) */
    uint8_t btn_lo = psx_xfer_byte_gap(0x00);

    int16_t joy_rx = 0, joy_ry = 0, joy_lx = 0, joy_ly = 0;
    int analog_reply = analog && (id == 0x73 || id == 0x53);
    uint8_t btn_hi = analog_reply ? psx_xfer_byte_gap(0x00)
                                  : psx_xfer_byte(0x00);

    /* Analog mode (id = 0x73 for DualShock, 0x53 for analog joystick) */
    if (analog_reply) {
        joy_rx = psx_axis_to_input(psx_xfer_byte_gap(0x00));
        joy_ry = psx_axis_to_input(psx_xfer_byte_gap(0x00));
        joy_lx = psx_axis_to_input(psx_xfer_byte_gap(0x00));
        joy_ly = psx_axis_to_input(psx_xfer_byte(0x00));
    }

    /* Deassert ATT */
    snac_gpio_set(idle_out, dir);

    if (!psx_response_valid(id, status))
        return 0;

    /* Map PSX buttons to Pocket format.
     * PSX byte layout (active low):
     * btn_lo: Select L3 R3 Start Up Right Down Left
     * btn_hi: L2 R2 L1 R1 Triangle Circle Cross Square */
    uint16_t raw = ~(((uint16_t)btn_hi << 8) | btn_lo);
    uint16_t buttons = 0;

    if (raw & 0x0001) buttons |= OF_BTN_SELECT;
    if (raw & 0x0002) buttons |= OF_BTN_L3;
    if (raw & 0x0004) buttons |= OF_BTN_R3;
    if (raw & 0x0008) buttons |= OF_BTN_START;
    if (raw & 0x0010) buttons |= OF_BTN_UP;
    if (raw & 0x0020) buttons |= OF_BTN_RIGHT;
    if (raw & 0x0040) buttons |= OF_BTN_DOWN;
    if (raw & 0x0080) buttons |= OF_BTN_LEFT;
    if (raw & 0x0100) buttons |= OF_BTN_L2;
    if (raw & 0x0200) buttons |= OF_BTN_R2;
    if (raw & 0x0400) buttons |= OF_BTN_L1;
    if (raw & 0x0800) buttons |= OF_BTN_R1;
    if (raw & 0x1000) buttons |= OF_BTN_X;       /* Triangle/top */
    if (raw & 0x2000) buttons |= OF_BTN_A;       /* Circle/right */
    if (raw & 0x4000) buttons |= OF_BTN_B;       /* Cross/bottom */
    if (raw & 0x8000) buttons |= OF_BTN_Y;       /* Square/left */

    if (commit) {
        snac_controller_t next = {
            .buttons = buttons,
            .joy_lx = joy_lx,
            .joy_ly = joy_ly,
            .joy_rx = joy_rx,
            .joy_ry = joy_ry,
        };
        snac_commit_state(0, &next);
    }

    return 1;
}

static void poll_psx(int analog) {
    if (poll_psx_att(analog, psx_att_pin, 1)) {
        psx_att_locked = 1;
        psx_invalid_poll_count = 0;
        return;
    }

    if (psx_att_locked) {
        /* Once a working ATT/select pin is known, do not probe the
         * alternate pin on every transient bad frame.  That extra failed
         * transaction can disturb some adapters and adds input latency. */
        if (psx_invalid_poll_count < PSX_CLEAR_AFTER_INVALID_POLLS) {
            psx_invalid_poll_count++;
            return;
        }

        psx_att_locked = 0;
    }

    uint8_t alt_att = (psx_att_pin == SNAC_PIN_OUT1)
                    ? SNAC_PIN_OUT2
                    : SNAC_PIN_OUT1;
    if (poll_psx_att(analog, alt_att, 1)) {
        psx_att_pin = alt_att;
        psx_att_locked = 1;
        psx_invalid_poll_count = 0;
        return;
    }

    /* Treat invalid PSX frames as transient transport failures, not as
     * releases.  Some adapters/controllers occasionally miss a reply while
     * a button is held; committing an empty state here creates visible
     * held-button flicker.  A real disconnect still clears after a short
     * invalid streak, so releases do not feel sticky. */
    if (psx_invalid_poll_count < PSX_CLEAR_AFTER_INVALID_POLLS) {
        psx_invalid_poll_count++;
        return;
    }

    snac_commit_empty(0);
}

/* ============================================================
 * Protocol: PC Engine (GPIO bit-bang)
 * CLR = OUT1 (pin[0]), SEL = OUT2 (pin[1])
 * D0-D3 on: IN4(pin[5]), IO3(pin[2]), IO6(pin[7]), IN7(pin[3])
 * ============================================================ */

static void poll_pce(int sixbtn) {
    /* Set directions: OUT1/OUT2 output, data pins input */
    uint8_t dir = 0x03;  /* bits [1:0] always output */

    /* Read first nibble: SEL=1, CLR=1 → D-pad */
    snac_gpio_set(SNAC_PIN_OUT1 | SNAC_PIN_OUT2, dir);
    for (volatile int i = 0; i < 20; i++) ;
    /* Pulse CLR low */
    snac_gpio_set(SNAC_PIN_OUT2, dir);  /* CLR=0, SEL=1 */
    for (volatile int i = 0; i < 20; i++) ;
    snac_gpio_set(SNAC_PIN_OUT1 | SNAC_PIN_OUT2, dir);  /* CLR=1, SEL=1 */
    for (volatile int i = 0; i < 20; i++) ;

    uint8_t pins = snac_gpio_read();
    /* Read D0-D3: D0=IN4(bit5), D1=IO3(bit2), D2=IO6(bit7), D3=IN7(bit3) */
    uint8_t nib0 = ((pins >> 5) & 1) | (((pins >> 2) & 1) << 1) |
                   (((pins >> 7) & 1) << 2) | (((pins >> 3) & 1) << 3);

    /* Second nibble: SEL=0, CLR=1 → buttons */
    snac_gpio_set(SNAC_PIN_OUT1, dir);  /* CLR=1, SEL=0 */
    for (volatile int i = 0; i < 20; i++) ;
    pins = snac_gpio_read();
    uint8_t nib1 = ((pins >> 5) & 1) | (((pins >> 2) & 1) << 1) |
                   (((pins >> 7) & 1) << 2) | (((pins >> 3) & 1) << 3);

    /* Map PC Engine buttons to Pocket format.
     * nib0 (D-pad, active low): D0=Up, D1=Right, D2=Down, D3=Left
     * nib1 (buttons, active low): D0=I, D1=II, D2=Select, D3=Run */
    uint16_t buttons = 0;
    nib0 = ~nib0 & 0x0F;
    nib1 = ~nib1 & 0x0F;

    if (nib0 & 0x01) buttons |= OF_BTN_UP;
    if (nib0 & 0x02) buttons |= OF_BTN_RIGHT;
    if (nib0 & 0x04) buttons |= OF_BTN_DOWN;
    if (nib0 & 0x08) buttons |= OF_BTN_LEFT;
    if (nib1 & 0x01) buttons |= OF_BTN_A;       /* I */
    if (nib1 & 0x02) buttons |= OF_BTN_B;       /* II */
    if (nib1 & 0x04) buttons |= OF_BTN_SELECT;
    if (nib1 & 0x08) buttons |= OF_BTN_START;   /* Run */

    if (sixbtn) {
        /* 6-button: third and fourth nibbles via additional SEL toggles */
        snac_gpio_set(SNAC_PIN_OUT1 | SNAC_PIN_OUT2, dir);  /* SEL=1 */
        for (volatile int i = 0; i < 20; i++) ;
        pins = snac_gpio_read();
        uint8_t nib2 = ((pins >> 5) & 1) | (((pins >> 2) & 1) << 1) |
                       (((pins >> 7) & 1) << 2) | (((pins >> 3) & 1) << 3);
        nib2 = ~nib2 & 0x0F;

        snac_gpio_set(SNAC_PIN_OUT1, dir);  /* SEL=0 */
        for (volatile int i = 0; i < 20; i++) ;
        pins = snac_gpio_read();
        uint8_t nib3 = ((pins >> 5) & 1) | (((pins >> 2) & 1) << 1) |
                       (((pins >> 7) & 1) << 2) | (((pins >> 3) & 1) << 3);
        nib3 = ~nib3 & 0x0F;

        /* 6-btn extra: III=X, IV=Y, V=L1, VI=R1 */
        if (nib2 & 0x01) buttons |= OF_BTN_X;
        if (nib2 & 0x02) buttons |= OF_BTN_Y;
        if (nib2 & 0x04) buttons |= OF_BTN_L1;
        if (nib2 & 0x08) buttons |= OF_BTN_R1;
    }

    snac_controller_t next = {
        .buttons = buttons,
        .joy_lx = 0,
        .joy_ly = 0,
        .joy_rx = 0,
        .joy_ry = 0,
    };
    snac_commit_state(0, &next);
}

/* ============================================================
 * Public API
 * ============================================================ */

void snac_init(uint8_t snac_type) {
    snac_active_flag = 0;
    snac_hw_active_flag = 0;
    snac_poll_busy = 0;
    active_type = snac_type;
    psx_att_pin = SNAC_PIN_OUT1;
    psx_att_locked = 0;
    psx_invalid_poll_count = 0;
    snac_last_poll_cycles = 0;
    snac_state[0] = (snac_controller_t){0};
    snac_state[1] = (snac_controller_t){0};
    SNAC_HW_CTRL = 0;
    SNAC_HW_CLEAR = SNAC_HW_CLEAR_IRQ | SNAC_HW_CLEAR_EDGES;

    if (snac_type == SNAC_NONE) {
        /* Disable SNAC mode → UART active */
        snac_gpio_out_shadow = 0;
        SNAC_CTRL = 0;
        return;
    }

    if (snac_type_is_psx(snac_type)) {
        /* Analog is auto-detected by the hardware poller from the pad's mode
         * ID (digital pads center the sticks), so only the bus-speed option
         * remains. */
        uint32_t hw_ctrl = SNAC_HW_CTRL_ENABLE;
        if (snac_type == SNAC_PSX_FAST)
            hw_ctrl |= SNAC_HW_CTRL_FAST;

        snac_hw_active_flag = 1;
        snac_active_flag = 1;
        SNAC_CTRL = 0;
        SNAC_HW_CTRL = hw_ctrl;
        return;
    }

    /* Enable SNAC mode (disables UART on shared pins) */
    uint32_t ctrl = SNAC_CTRL_ENABLE;

    if (snac_type >= SNAC_PCE_2BTN && snac_type <= SNAC_PCE_MULTITAP) {
        /* PC Engine: pure GPIO, no shifter.  Use Config A mode but
         * we won't actually use the shifter. */
        ctrl |= SNAC_CTRL_MODE_A;
        /* GPIO: OUT1=CLR, OUT2=SEL — both high initially */
        snac_gpio_set(SNAC_PIN_OUT1 | SNAC_PIN_OUT2, 0x03);
    } else {
        /* Config A (NES/SNES/DB15): CLK on OUT1, LATCH on OUT2, DATA on IO3 */
        ctrl |= SNAC_CTRL_MODE_A;
        /* NES/SNES: 200 KHz clock -> div = CPU_FREQ/(2*200000) - 1 = 249 */
        SNAC_DIV = 249;
        /* GPIO: OUT1=CLK, OUT2=LATCH — both idle low */
        snac_gpio_set(0, 0x03);
        if (snac_type == SNAC_DB15_FAST)
            SNAC_DIV = 124;  /* 400 KHz */
    }

    SNAC_CTRL = ctrl;
    snac_active_flag = 1;
    snac_poll();
}

void snac_poll(void) {
    if (!snac_active_flag) return;
    if (snac_hw_active_flag) return;
    if (snac_poll_busy) return;

    snac_poll_busy = 1;

    switch (active_type) {
    case SNAC_NES:
        poll_serlatch(8);
        break;
    case SNAC_SNES:
    case SNAC_SNES_SWAP:
        poll_serlatch(16);
        break;
    case SNAC_DB15:
    case SNAC_DB15_FAST:
        poll_serlatch(12);
        break;
    case SNAC_PSX:
    case SNAC_PSX_FAST:
        /* Software fallback (normally unreachable: PSX uses the HW poller).
         * Analog is auto-detected per poll from the controller ID. */
        poll_psx(1);
        break;
    case SNAC_PCE_2BTN:
        poll_pce(0);
        break;
    case SNAC_PCE_6BTN:
        poll_pce(1);
        break;
    case SNAC_PCE_MULTITAP:
        poll_pce(0);  /* TODO: read multiple players via multitap scan */
        break;
    default:
        break;
    }

    snac_last_poll_cycles = read_cycles();
    snac_poll_busy = 0;
}

void snac_poll_if_due(uint32_t min_interval_us) {
    if (!snac_active_flag) return;
    if (snac_hw_active_flag) return;

    uint64_t now = read_cycles();
    uint64_t min_cycles =
        (uint64_t)min_interval_us * (CPU_FREQ_HZ / 1000000u);

    if (snac_last_poll_cycles &&
        (now - snac_last_poll_cycles) < min_cycles)
        return;

    snac_poll();
}

snac_controller_t snac_read_state(int player) {
    snac_controller_t out;

    if (player < 0 || player > 1)
        player = 0;

    if (snac_hw_active_flag) {
        uint32_t joy_l = SNAC_HW_JOY_L;
        uint32_t joy_r = SNAC_HW_JOY_R;
        out = (snac_controller_t){
            .buttons = (uint16_t)SNAC_HW_BUTTONS,
            .buttons_pressed = (uint16_t)SNAC_HW_PRESSED,
            .buttons_released = (uint16_t)SNAC_HW_RELEASED,
            .joy_lx = (int16_t)(joy_l & 0xFFFFu),
            .joy_ly = (int16_t)(joy_l >> 16),
            .joy_rx = (int16_t)(joy_r & 0xFFFFu),
            .joy_ry = (int16_t)(joy_r >> 16),
        };
        snac_state[0] = out;
        SNAC_HW_CLEAR = SNAC_HW_CLEAR_EDGES;
        return out;
    }

    out = snac_state[player];
    snac_state[player].buttons_pressed = 0;
    snac_state[player].buttons_released = 0;
    return out;
}

void snac_irq_ack(void) {
    if (snac_hw_active_flag)
        SNAC_HW_CLEAR = SNAC_HW_CLEAR_IRQ;
}

int snac_is_active(void) {
    return snac_active_flag;
}
