/*
 * of_error.h -- Unified error codes for openfpgaOS
 */

#ifndef OF_ERROR_H
#define OF_ERROR_H

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    OF_OK           =  0,
    OF_ERR_TIMEOUT  = -1,
    OF_ERR_IO       = -2,
    OF_ERR_PARAM    = -3,
    OF_ERR_BUSY     = -4,
    OF_ERR_NOSYS    = -5,
    OF_ERR_NOMEM    = -6,
} of_error_t;

/* Boolean parameter constants -- pass these to APIs that take an `int
 * enable` / `int loop` / `int hflip` flag for self-documenting call sites:
 *   of_tile_enable(OF_ENABLE, 0);
 *   of_midi_play(data, len, OF_LOOP);
 */
#define OF_DISABLE  0
#define OF_ENABLE   1
#define OF_OFF      0
#define OF_ON       1
#define OF_NO       0
#define OF_YES      1
#define OF_ONCE     0
#define OF_LOOP     1

#ifdef __cplusplus
}
#endif

#endif /* OF_ERROR_H */
