#ifndef SM64_OVERLAP_COUPLED_H
#define SM64_OVERLAP_COUPLED_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
int coupled_active(void);
void coupled_start(void *env, const char *out);
void coupled_sync(void);
void coupled_write(uint32_t off, uint32_t value);
uint32_t coupled_read(uint32_t off);
void coupled_submit(uint32_t idx, uint32_t token, uint32_t game_tick);
int coupled_acquire(int just, uint32_t token);
void coupled_video_timing(uint32_t addr);
void coupled_finish(void);
void coupled_close(void);
extern uint32_t coupled_render_pc;
void coupled_render_enter(void);
#ifdef __cplusplus
}
#endif
#endif
