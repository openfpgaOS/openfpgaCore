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

    snac_state[0].buttons = buttons;
    snac_state[0].joy_lx = 0;
    snac_state[0].joy_ly = 0;
    snac_state[0].joy_rx = 0;
    snac_state[0].joy_ry = 0;
}

/* ============================================================
 * Protocol: PlayStation (Config B)
 * SPI-based: CLK = IO3 (pin[2]), CMD = IO6 (pin[7]), DAT = IN4 (pin[5])
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

static void poll_psx(int analog) {
    /* Assert ATT (active low) */
    uint8_t gpio_out = snac_gpio_read();
    snac_gpio_write(gpio_out & ~SNAC_PIN_OUT1,
                    SNAC_DIR_IO3 >> 8 | SNAC_DIR_IO6 >> 8 | 0x03);

    /* Small delay for ATT setup */
    for (volatile int i = 0; i < 10; i++) ;

    /* Address: 0x01 */
    psx_xfer_byte(0x01);
    /* Command: 0x42 (Read Data), get device ID */
    uint8_t id = psx_xfer_byte(0x42);
    /* Get status byte (usually 0x5A = ready) */
    psx_xfer_byte(0x00);
    /* Button bytes (active low) */
    uint8_t btn_lo = psx_xfer_byte(0x00);
    uint8_t btn_hi = psx_xfer_byte(0x00);

    uint8_t rx_val = 0, ry_val = 0, lx_val = 0, ly_val = 0;

    /* Analog mode (id = 0x73 for DualShock, 0x53 for analog joystick) */
    if (analog && (id == 0x73 || id == 0x53)) {
        rx_val = psx_xfer_byte(0x00);
        ry_val = psx_xfer_byte(0x00);
        lx_val = psx_xfer_byte(0x00);
        ly_val = psx_xfer_byte(0x00);
    }

    /* Deassert ATT */
    snac_gpio_write(gpio_out | SNAC_PIN_OUT1,
                    SNAC_DIR_IO3 >> 8 | SNAC_DIR_IO6 >> 8 | 0x03);

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
    if (raw & 0x1000) buttons |= OF_BTN_Y;       /* Triangle */
    if (raw & 0x2000) buttons |= OF_BTN_B;       /* Circle */
    if (raw & 0x4000) buttons |= OF_BTN_A;       /* Cross */
    if (raw & 0x8000) buttons |= OF_BTN_X;       /* Square */

    snac_state[0].buttons = buttons;
    /* Analog: PSX sticks center at 128, range 0-255 → map to -128..+127 */
    snac_state[0].joy_lx = (int8_t)(lx_val - 128);
    snac_state[0].joy_ly = (int8_t)(ly_val - 128);
    snac_state[0].joy_rx = (int8_t)(rx_val - 128);
    snac_state[0].joy_ry = (int8_t)(ry_val - 128);
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
    snac_gpio_write(SNAC_PIN_OUT1 | SNAC_PIN_OUT2, dir);
    for (volatile int i = 0; i < 20; i++) ;
    /* Pulse CLR low */
    snac_gpio_write(SNAC_PIN_OUT2, dir);  /* CLR=0, SEL=1 */
    for (volatile int i = 0; i < 20; i++) ;
    snac_gpio_write(SNAC_PIN_OUT1 | SNAC_PIN_OUT2, dir);  /* CLR=1, SEL=1 */
    for (volatile int i = 0; i < 20; i++) ;

    uint8_t pins = snac_gpio_read();
    /* Read D0-D3: D0=IN4(bit5), D1=IO3(bit2), D2=IO6(bit7), D3=IN7(bit3) */
    uint8_t nib0 = ((pins >> 5) & 1) | (((pins >> 2) & 1) << 1) |
                   (((pins >> 7) & 1) << 2) | (((pins >> 3) & 1) << 3);

    /* Second nibble: SEL=0, CLR=1 → buttons */
    snac_gpio_write(SNAC_PIN_OUT1, dir);  /* CLR=1, SEL=0 */
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
        snac_gpio_write(SNAC_PIN_OUT1 | SNAC_PIN_OUT2, dir);  /* SEL=1 */
        for (volatile int i = 0; i < 20; i++) ;
        pins = snac_gpio_read();
        uint8_t nib2 = ((pins >> 5) & 1) | (((pins >> 2) & 1) << 1) |
                       (((pins >> 7) & 1) << 2) | (((pins >> 3) & 1) << 3);
        nib2 = ~nib2 & 0x0F;

        snac_gpio_write(SNAC_PIN_OUT1, dir);  /* SEL=0 */
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

    snac_state[0].buttons = buttons;
    snac_state[0].joy_lx = 0;
    snac_state[0].joy_ly = 0;
    snac_state[0].joy_rx = 0;
    snac_state[0].joy_ry = 0;
}

/* ============================================================
 * Public API
 * ============================================================ */

void snac_init(uint8_t snac_type) {
    active_type = snac_type;
    snac_state[0] = (snac_controller_t){0};
    snac_state[1] = (snac_controller_t){0};

    if (snac_type == SNAC_NONE) {
        snac_active_flag = 0;
        /* Disable SNAC mode → UART active */
        SNAC_CTRL = 0;
        return;
    }

    snac_active_flag = 1;

    /* Enable SNAC mode (disables UART on shared pins) */
    uint32_t ctrl = SNAC_CTRL_ENABLE;

    if (snac_type >= SNAC_PSX) {
        /* Config B (PSX): CLK on IO3, CMD on IO6, DAT on IN4 */
        ctrl |= SNAC_CTRL_MODE_B;
        /* PSX standard: 125 KHz clock → div = CPU_FREQ/(2*125000) - 1 = 399 */
        SNAC_DIV = 399;
        /* GPIO: OUT1=ATT high (deassert), IO3=CLK out, IO6=CMD out */
        snac_gpio_write(SNAC_PIN_OUT1 | SNAC_PIN_OUT2 | SNAC_PIN_IO3 | SNAC_PIN_IO6,
                        (SNAC_DIR_IO3 | SNAC_DIR_IO6) >> 8 | 0x03);
        if (snac_type == SNAC_PSX_FAST || snac_type == SNAC_PSX_ANALOG_FAST)
            SNAC_DIV = 199;  /* 250 KHz */
    } else if (snac_type >= SNAC_PCE_2BTN && snac_type <= SNAC_PCE_MULTITAP) {
        /* PC Engine: pure GPIO, no shifter.  Use Config A mode but
         * we won't actually use the shifter. */
        ctrl |= SNAC_CTRL_MODE_A;
        /* GPIO: OUT1=CLR, OUT2=SEL — both high initially */
        snac_gpio_write(SNAC_PIN_OUT1 | SNAC_PIN_OUT2, 0x03);
    } else {
        /* Config A (NES/SNES/DB15): CLK on OUT1, LATCH on OUT2, DATA on IO3 */
        ctrl |= SNAC_CTRL_MODE_A;
        /* NES/SNES: 200 KHz clock → div = CPU_FREQ/(2*200000) - 1 = 249 */
        SNAC_DIV = 249;
        /* GPIO: OUT1=CLK, OUT2=LATCH — both idle low */
        snac_gpio_write(0, 0x03);
        if (snac_type == SNAC_DB15_FAST)
            SNAC_DIV = 124;  /* 400 KHz */
    }

    SNAC_CTRL = ctrl;
}

void snac_poll(void) {
    if (!snac_active_flag) return;

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
        poll_psx(0);
        break;
    case SNAC_PSX_ANALOG:
    case SNAC_PSX_ANALOG_FAST:
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
}

const snac_controller_t *snac_get_state(int player) {
    if (player < 0 || player > 1) return &snac_state[0];
    return &snac_state[player];
}

int snac_is_active(void) {
    return snac_active_flag;
}
