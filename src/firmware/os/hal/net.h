//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Network HAL
 * Host/client byte-stream API over platform transport
 * (Pocket: link cable, MiSTer: user port, PC: sockets)
 */

#ifndef OFOS_NET_H
#define OFOS_NET_H

#include <stdint.h>
#include <stddef.h>

#define OF_NET_DISCONNECTED  0
#define OF_NET_HOSTING       1
#define OF_NET_JOINED        2

/* Connection management */
int  of_net_host_start(void);
int  of_net_join(void);
void of_net_stop(void);
int  of_net_status(void);

/* Host API */
int  of_net_client_count(void);
int  of_net_send_to(int client, const void *data, size_t len);
int  of_net_recv_from(int client, void *data, size_t len);
int  of_net_broadcast(const void *data, size_t len);

/* Client API */
int  of_net_send(const void *data, size_t len);
int  of_net_recv(void *data, size_t len);

/* Poll network state — call frequently */
int  of_net_poll(void);

/* SBI vendor dispatcher for OF_EID_NET (called from syscall.c) */
long of_net_dispatch(long fid, long a0, long a1, long a2);

#endif /* OFOS_NET_H */
