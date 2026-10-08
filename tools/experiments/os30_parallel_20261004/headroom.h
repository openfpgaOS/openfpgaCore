#ifndef OS30_PARALLEL_HEADROOM_H
#define OS30_PARALLEL_HEADROOM_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
void headroom_begin(void *environment, const char *folder);
void headroom_instruction(uint64_t raw_cycle, uint32_t pc);
uint64_t headroom_credit(uint64_t raw_cycle);
void headroom_finish(uint64_t raw_cycle);
#ifdef __cplusplus
}
#endif
#endif
