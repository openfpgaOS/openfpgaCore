/*
 * openfpgaOS Terminal HAL
 * 40x30 character text terminal with 16-color support
 */

#ifndef OFOS_TERMINAL_H
#define OFOS_TERMINAL_H

#include <stdint.h>

/* 16-color palette (VGA/CGA-style) */
#define OF_COLOR_BLACK          0
#define OF_COLOR_DARK_BLUE      1
#define OF_COLOR_DARK_GREEN     2
#define OF_COLOR_DARK_CYAN      3
#define OF_COLOR_DARK_RED       4
#define OF_COLOR_DARK_MAGENTA   5
#define OF_COLOR_BROWN          6
#define OF_COLOR_LIGHT_GRAY     7
#define OF_COLOR_DARK_GRAY      8
#define OF_COLOR_BLUE           9
#define OF_COLOR_GREEN          10
#define OF_COLOR_CYAN           11
#define OF_COLOR_RED            12
#define OF_COLOR_MAGENTA        13
#define OF_COLOR_YELLOW         14
#define OF_COLOR_WHITE          15

/* Initialize terminal (clear screen, reset cursor, default colors) */
void of_term_init(void);

/* Print a single character (handles \n, \r, \b) */
void of_term_putchar(char c);

/* Print a null-terminated string */
void of_term_puts(const char *s);

/* Formatted print (supports %d, %u, %x, %X, %s, %c, %p, %%) */
void of_term_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

/* Clear the terminal screen */
void of_term_clear(void);

/* Set cursor position */
void of_term_set_pos(int col, int row);

/* Get cursor position */
void of_term_get_pos(int *col, int *row);

/* Scroll the terminal up by one line */
void of_term_scroll(void);

/* Set foreground and background color for subsequent output */
void of_term_set_color(uint8_t fg, uint8_t bg);

/* Get current foreground and background color */
void of_term_get_color(uint8_t *fg, uint8_t *bg);

/* Set a single cell's character and color directly */
void of_term_set_cell(int col, int row, char ch, uint8_t fg, uint8_t bg);

/* Re-render entire terminal buffer to the current framebuffer surface.
 * Call after of_video_flip() to sync triple-buffered content. */
void of_term_redraw(void);

/* Reset ANSI escape parser and restore default colors */
void of_term_reset(void);

/* Set terminal display mode: 0=terminal, 1=framebuffer, 2=overlay */
void of_term_set_display_mode(int mode);

/* Enable UART console mirror after boot has confirmed the cart pins are free. */
void of_term_enable_uart_mirror(void);

/* Service target-specific terminal UART output.  Pocket output is
 * synchronous, so this is a compatibility no-op there. */
void of_term_uart_drain(void);

#endif /* OFOS_TERMINAL_H */
