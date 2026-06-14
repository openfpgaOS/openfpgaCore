//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * menu.elf — openfpgaOS game launcher (Architecture A).
 *
 * Lists the game instances under /games (each a directory carrying an os.ini),
 * lets the player pick one with the d-pad, and asks the OS to relaunch into it
 * (of_relaunch) without an FPGA reset.  Registers itself as the exit-to
 * launcher so a game's exit() returns here.
 *
 * MiSTer only: on the Pocket the Analogue host provides the instance browser,
 * so of_instance_list() returns 0 and of_relaunch() is OF_ERR_NOT_SUPPORTED —
 * this app simply shows "no games" there and is not part of the Pocket flow.
 *
 * Deployed as the image-root /app.elf (slot 3): the root /os.ini launches it,
 * and exit-to-menu relaunches it via of_relaunch("", "app.elf").
 */

#include "of.h"
#include <stdio.h>

#define MAX_INSTANCES 64
#define NAME_STRIDE   28      /* bytes per instance name in the flat buffer  */
#define VISIBLE_ROWS  22      /* 40x30 terminal, minus header/footer chrome  */

static char g_names[MAX_INSTANCES * NAME_STRIDE];

static const char *inst(int i) { return &g_names[i * NAME_STRIDE]; }

/* ~60 Hz pacing without a video/vsync dependency (this is a text UI). */
static void frame_wait(void) {
    unsigned t0 = of_time_ms();
    while ((unsigned)(of_time_ms() - t0) < 16u) { }
}

static void draw(int count, int sel, int top) {
    printf("\033[2J\033[H");
    printf("  \033[96mopenfpgaOS\033[0m  \033[90m// game launcher\033[0m\n\n");

    if (count <= 0) {
        printf("  \033[93mNo games found under /games/\033[0m\n\n");
        printf("  Install a game as:\n");
        printf("    /games/<name>/os.ini\n");
        printf("    /games/<name>/app.elf\n");
        return;
    }

    for (int r = 0; r < VISIBLE_ROWS && top + r < count; r++) {
        int i = top + r;
        if (i == sel)
            printf("  \033[30;47m %c %-*s \033[0m\n",
                   ACS_RARROW, NAME_STRIDE - 1, inst(i));
        else
            printf("    \033[97m%-*s\033[0m\n", NAME_STRIDE - 1, inst(i));
    }

    if (count > VISIBLE_ROWS)
        printf("\n  \033[90m%d games  (%d-%d)\033[0m\n",
               count, top + 1,
               (top + VISIBLE_ROWS < count) ? top + VISIBLE_ROWS : count);

    printf("\n  \033[90m%c%c move   A / START  launch\033[0m\n",
           ACS_UARROW, ACS_DARROW);
}

int main(void) {
    /* Unbuffered stdout so each redraw reaches the terminal immediately. */
    setvbuf(stdout, NULL, _IONBF, 0);

    int count = of_instance_list(g_names, NAME_STRIDE, MAX_INSTANCES);

    /* Return to this launcher when a started game exit()s. */
    of_launch_set_menu("", "app.elf");

    int sel = 0, top = 0, dirty = 1;

    for (;;) {
        of_input_poll();

        if (count > 0) {
            int moved = 0;
            if (of_btn_pressed(OF_BTN_DOWN) && sel < count - 1) { sel++; moved = 1; }
            if (of_btn_pressed(OF_BTN_UP)   && sel > 0)         { sel--; moved = 1; }
            if (moved) {
                if (sel < top)                 top = sel;
                if (sel >= top + VISIBLE_ROWS) top = sel - (VISIBLE_ROWS - 1);
                dirty = 1;
            }

            if (of_btn_pressed(OF_BTN_A) || of_btn_pressed(OF_BTN_START)) {
                char path[64];
                snprintf(path, sizeof(path), "/games/%s", inst(sel));
                printf("\033[2J\033[H\n  \033[92mLaunching %s ...\033[0m\n",
                       inst(sel));
                of_relaunch(path, NULL);     /* does not return on success */
                /* Only reached if the relaunch failed (bad/missing ELF). */
                printf("  \033[91mLaunch failed: %s\033[0m\n", inst(sel));
                dirty = 1;
            }
        }

        if (dirty) { draw(count, sel, top); dirty = 0; }
        frame_wait();
    }

    return 0;
}
