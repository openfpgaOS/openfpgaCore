/*
 * openfpgaOS Terminal HAL Implementation
 * 40x30 character VRAM at 0x20000000, color VRAM at 0x20000800
 */

#include "terminal.h"
#include "regs.h"
#include <stdarg.h>

static int term_col = 0;
static int term_row = 0;
static uint8_t term_fg = OF_COLOR_WHITE;
static uint8_t term_bg = OF_COLOR_BLACK;

/* ANSI escape sequence parser state */
#define ESC_NONE    0   /* normal character output */
#define ESC_ESC     1   /* saw \033 */
#define ESC_CSI     2   /* saw \033[ — collecting parameters */

static int esc_state = ESC_NONE;
#define ESC_MAX_PARAMS 4
static int esc_params[ESC_MAX_PARAMS];
static int esc_nparam;
static int esc_cur_param;

/* ANSI color index → OF_COLOR_* (maps SGR 30-37 to VGA palette) */
static const uint8_t ansi_to_color[8] = {
    OF_COLOR_BLACK, OF_COLOR_DARK_RED, OF_COLOR_DARK_GREEN, OF_COLOR_BROWN,
    OF_COLOR_DARK_BLUE, OF_COLOR_DARK_MAGENTA, OF_COLOR_DARK_CYAN, OF_COLOR_LIGHT_GRAY
};
static const uint8_t ansi_to_bright[8] = {
    OF_COLOR_DARK_GRAY, OF_COLOR_RED, OF_COLOR_GREEN, OF_COLOR_YELLOW,
    OF_COLOR_BLUE, OF_COLOR_MAGENTA, OF_COLOR_CYAN, OF_COLOR_WHITE
};

static inline uint8_t term_color_byte(uint8_t fg, uint8_t bg) {
    return (uint8_t)((bg & 0xF) << 4) | (fg & 0xF);
}

static inline void term_write_char(int col, int row, char c) {
    if (col < TERM_COLS && row < TERM_ROWS)
        REG8(TERM_VRAM_BASE + row * TERM_COLS + col) = c;
}

static inline void term_write_color(int col, int row, uint8_t color) {
    if (col < TERM_COLS && row < TERM_ROWS)
        REG8(TERM_COLOR_BASE + row * TERM_COLS + col) = color;
}

static inline char term_read_char(int col, int row) {
    if (col < TERM_COLS && row < TERM_ROWS)
        return REG8(TERM_VRAM_BASE + row * TERM_COLS + col);
    return ' ';
}

static inline uint8_t term_read_color(int col, int row) {
    if (col < TERM_COLS && row < TERM_ROWS)
        return REG8(TERM_COLOR_BASE + row * TERM_COLS + col);
    return 0x0F;
}

static inline void term_write_cell(int col, int row, char c, uint8_t color) {
    term_write_char(col, row, c);
    term_write_color(col, row, color);
}

void of_term_init(void) {
    term_fg = OF_COLOR_WHITE;
    term_bg = OF_COLOR_BLACK;
    of_term_clear();
    term_col = 0;
    term_row = 0;
}

void of_term_clear(void) {
    /* Always clear to white-on-black and reset color state */
    term_fg = OF_COLOR_WHITE;
    term_bg = OF_COLOR_BLACK;
    esc_state = ESC_NONE;
    for (int i = 0; i < TERM_COLS * TERM_ROWS; i++)
        REG8(TERM_VRAM_BASE + i) = ' ';
    for (int i = 0; i < TERM_COLS * TERM_ROWS; i++)
        REG8(TERM_COLOR_BASE + i) = 0x0F;
    term_col = 0;
    term_row = 0;
}

void of_term_scroll(void) {
    /* Move all rows up by 1 (both char and color) */
    for (int row = 0; row < TERM_ROWS - 1; row++) {
        for (int col = 0; col < TERM_COLS; col++) {
            term_write_char(col, row, term_read_char(col, row + 1));
            term_write_color(col, row, term_read_color(col, row + 1));
        }
    }

    /* Clear bottom row */
    uint8_t color = term_color_byte(term_fg, term_bg);
    for (int col = 0; col < TERM_COLS; col++) {
        term_write_char(col, TERM_ROWS - 1, ' ');
        term_write_color(col, TERM_ROWS - 1, color);
    }
}

/* Handle a completed CSI sequence: \033[params...X */
static void esc_dispatch(char cmd) {
    int p0 = esc_nparam > 0 ? esc_params[0] : 0;
    int p1 = esc_nparam > 1 ? esc_params[1] : 0;

    switch (cmd) {
    case 'H': case 'f':  /* Cursor position: \033[row;colH */
        term_row = (p0 > 0 ? p0 - 1 : 0);
        term_col = (p1 > 0 ? p1 - 1 : 0);
        if (term_row >= TERM_ROWS) term_row = TERM_ROWS - 1;
        if (term_col >= TERM_COLS) term_col = TERM_COLS - 1;
        break;

    case 'J':  /* Erase in display */
        if (p0 == 2)
            of_term_clear();
        break;

    case 'K':  /* Erase in line */
        if (p0 == 0) {
            uint8_t color = term_color_byte(term_fg, term_bg);
            for (int c = term_col; c < TERM_COLS; c++)
                term_write_cell(c, term_row, ' ', color);
        }
        break;

    case 'A':  /* Cursor up */
        term_row -= (p0 > 0 ? p0 : 1);
        if (term_row < 0) term_row = 0;
        break;
    case 'B':  /* Cursor down */
        term_row += (p0 > 0 ? p0 : 1);
        if (term_row >= TERM_ROWS) term_row = TERM_ROWS - 1;
        break;
    case 'C':  /* Cursor forward */
        term_col += (p0 > 0 ? p0 : 1);
        if (term_col >= TERM_COLS) term_col = TERM_COLS - 1;
        break;
    case 'D':  /* Cursor back */
        term_col -= (p0 > 0 ? p0 : 1);
        if (term_col < 0) term_col = 0;
        break;

    case 'm':  /* SGR — Select Graphic Rendition (colors) */
        for (int i = 0; i < esc_nparam; i++) {
            int v = esc_params[i];
            if (v == 0) {
                term_fg = OF_COLOR_WHITE;
                term_bg = OF_COLOR_BLACK;
            } else if (v >= 30 && v <= 37) {
                term_fg = ansi_to_color[v - 30];
            } else if (v >= 40 && v <= 47) {
                term_bg = ansi_to_color[v - 40];
            } else if (v >= 90 && v <= 97) {
                term_fg = ansi_to_bright[v - 90];
            } else if (v >= 100 && v <= 107) {
                term_bg = ansi_to_bright[v - 100];
            } else if (v == 1) {
                /* Bold — upgrade dark colors to bright */
                if (term_fg < 8)
                    term_fg = ansi_to_bright[term_fg];
            } else if (v == 7) {
                /* Reverse video */
                uint8_t tmp = term_fg;
                term_fg = term_bg;
                term_bg = tmp;
            }
        }
        if (esc_nparam == 0) {
            /* \033[m with no params = reset */
            term_fg = OF_COLOR_WHITE;
            term_bg = OF_COLOR_BLACK;
        }
        break;
    }
}

/* Emit a raw character (no escape processing) */
static void term_emit_char(char c) {
    uint8_t color = term_color_byte(term_fg, term_bg);

    if (c == '\n') {
        term_col = 0;
        term_row++;
        if (term_row >= TERM_ROWS) {
            of_term_scroll();
            term_row = TERM_ROWS - 1;
        }
    } else if (c == '\r') {
        term_col = 0;
    } else if (c == '\b') {
        if (term_col > 0) {
            term_col--;
            term_write_cell(term_col, term_row, ' ', color);
        }
    } else {
        term_write_cell(term_col, term_row, c, color);
        term_col++;
        if (term_col >= TERM_COLS) {
            term_col = 0;
            term_row++;
            if (term_row >= TERM_ROWS) {
                of_term_scroll();
                term_row = TERM_ROWS - 1;
            }
        }
    }
}

void of_term_putchar(char c) {
    switch (esc_state) {
    case ESC_NONE:
        if (c == '\033') {
            esc_state = ESC_ESC;
        } else {
            term_emit_char(c);
        }
        break;

    case ESC_ESC:
        if (c == '[') {
            esc_state = ESC_CSI;
            esc_nparam = 0;
            esc_cur_param = 0;
            for (int i = 0; i < ESC_MAX_PARAMS; i++)
                esc_params[i] = 0;
        } else {
            /* Not a CSI sequence — emit both chars */
            esc_state = ESC_NONE;
            term_emit_char('\033');
            term_emit_char(c);
        }
        break;

    case ESC_CSI:
        if (c >= '0' && c <= '9') {
            /* Accumulate digit into current parameter */
            esc_cur_param = esc_cur_param * 10 + (c - '0');
        } else if (c == ';') {
            /* Parameter separator */
            if (esc_nparam < ESC_MAX_PARAMS)
                esc_params[esc_nparam++] = esc_cur_param;
            esc_cur_param = 0;
        } else {
            /* End of sequence — finalize last param and dispatch */
            if (esc_nparam < ESC_MAX_PARAMS)
                esc_params[esc_nparam++] = esc_cur_param;
            esc_dispatch(c);
            esc_state = ESC_NONE;
        }
        break;
    }
}

void of_term_puts(const char *s) {
    while (*s)
        of_term_putchar(*s++);
}

void of_term_set_pos(int col, int row) {
    if (col >= 0 && col < TERM_COLS) term_col = col;
    if (row >= 0 && row < TERM_ROWS) term_row = row;
}

void of_term_get_pos(int *col, int *row) {
    if (col) *col = term_col;
    if (row) *row = term_row;
}

void of_term_set_color(uint8_t fg, uint8_t bg) {
    term_fg = fg & 0xF;
    term_bg = bg & 0xF;
}

void of_term_get_color(uint8_t *fg, uint8_t *bg) {
    if (fg) *fg = term_fg;
    if (bg) *bg = term_bg;
}

void of_term_set_cell(int col, int row, char ch, uint8_t fg, uint8_t bg) {
    term_write_cell(col, row, ch, term_color_byte(fg, bg));
}

void of_term_reset(void) {
    esc_state = ESC_NONE;
    term_fg = OF_COLOR_WHITE;
    term_bg = OF_COLOR_BLACK;
}

/* Minimal printf for terminal output */
static void put_uint(uint32_t val, int base, int uppercase,
                     int width, char pad) {
    char buf[12];
    int pos = 0;
    const char *digits = uppercase ? "0123456789ABCDEF" : "0123456789abcdef";

    if (val == 0) {
        buf[pos++] = '0';
    } else {
        while (val > 0) {
            buf[pos++] = digits[val % base];
            val /= base;
        }
    }

    /* Pad to width */
    while (pos < width)
        buf[pos++] = pad;

    while (pos > 0)
        of_term_putchar(buf[--pos]);
}

void of_term_printf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);

    while (*fmt) {
        if (*fmt != '%') {
            of_term_putchar(*fmt++);
            continue;
        }
        fmt++;

        /* Parse flags */
        char pad = ' ';
        if (*fmt == '0') {
            pad = '0';
            fmt++;
        }

        /* Parse width */
        int width = 0;
        while (*fmt >= '0' && *fmt <= '9') {
            width = width * 10 + (*fmt - '0');
            fmt++;
        }

        switch (*fmt) {
        case 'd': case 'i': {
            int val = va_arg(ap, int);
            if (val < 0) {
                of_term_putchar('-');
                val = -val;
                if (width > 0) width--;
            }
            put_uint((uint32_t)val, 10, 0, width, pad);
            break;
        }
        case 'u':
            put_uint(va_arg(ap, uint32_t), 10, 0, width, pad);
            break;
        case 'x':
            put_uint(va_arg(ap, uint32_t), 16, 0, width, pad);
            break;
        case 'X':
            put_uint(va_arg(ap, uint32_t), 16, 1, width, pad);
            break;
        case 'p':
            of_term_puts("0x");
            put_uint(va_arg(ap, uint32_t), 16, 0, 8, '0');
            break;
        case 's': {
            const char *s = va_arg(ap, const char *);
            of_term_puts(s ? s : "(null)");
            break;
        }
        case 'c':
            of_term_putchar((char)va_arg(ap, int));
            break;
        case '%':
            of_term_putchar('%');
            break;
        default:
            of_term_putchar('%');
            of_term_putchar(*fmt);
            break;
        }
        fmt++;
    }

    va_end(ap);
}
