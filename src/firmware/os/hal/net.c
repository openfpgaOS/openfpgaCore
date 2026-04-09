/*
 * openfpgaOS Network HAL — byte-stream framing over link cable
 *
 * Wraps the 32-bit link cable FIFO into a host/client byte-stream API.
 * On Pocket: 1 host, 1 client (single link cable).
 *
 * Wire protocol (length-prefix framing):
 *   Word 0: byte count (little-endian uint32_t)
 *   Words 1..N: data (padded to 4-byte boundary)
 */

#include "net.h"
#include "link.h"
#include "regs.h"
#include "of_syscall_numbers.h"

#define NET_RX_BUF_SIZE  4096

static int net_state = OF_NET_DISCONNECTED;
static uint8_t rx_buf[NET_RX_BUF_SIZE];
static uint32_t rx_head;  /* bytes available in rx_buf */

int of_net_host_start(void)
{
    net_state = OF_NET_HOSTING;
    rx_head = 0;
    return 0;
}

int of_net_join(void)
{
    net_state = OF_NET_JOINED;
    rx_head = 0;
    return 0;
}

void of_net_stop(void)
{
    net_state = OF_NET_DISCONNECTED;
    rx_head = 0;
}

int of_net_status(void)
{
    return net_state;
}

int of_net_client_count(void)
{
    if (net_state != OF_NET_HOSTING) return 0;
    /* On Pocket: 1 client max if link has received data */
    return of_link_rx_available() > 0 ? 1 : 0;
}

/* Send a byte buffer over the link (length-prefix framing) */
static int net_send_bytes(const void *data, size_t len)
{
    if (net_state == OF_NET_DISCONNECTED) return -1;

    /* Send length word */
    if (of_link_send((uint32_t)len) < 0) return -1;

    /* Send data words (4 bytes at a time) */
    const uint8_t *p = (const uint8_t *)data;
    size_t remaining = len;
    while (remaining > 0) {
        uint32_t word = 0;
        size_t chunk = remaining < 4 ? remaining : 4;
        for (size_t i = 0; i < chunk; i++)
            word |= (uint32_t)p[i] << (i * 8);
        if (of_link_send(word) < 0) return -1;
        p += chunk;
        remaining -= chunk;
    }
    return (int)len;
}

/* Receive bytes from the link into rx_buf */
static void net_drain_rx(void)
{
    uint32_t word;
    while (rx_head < NET_RX_BUF_SIZE - 4) {
        if (of_link_recv(&word) < 0) break;
        /* Store received word as bytes */
        size_t n = NET_RX_BUF_SIZE - rx_head;
        if (n > 4) n = 4;
        for (size_t i = 0; i < n; i++)
            rx_buf[rx_head++] = (uint8_t)(word >> (i * 8));
    }
}

int of_net_send_to(int client, const void *data, size_t len)
{
    (void)client;  /* Pocket: only client 0 */
    return net_send_bytes(data, len);
}

int of_net_recv_from(int client, void *data, size_t len)
{
    (void)client;
    net_drain_rx();
    size_t avail = rx_head < len ? rx_head : len;
    if (avail == 0) return 0;
    __builtin_memcpy(data, rx_buf, avail);
    /* Shift remaining data */
    if (avail < rx_head)
        __builtin_memmove(rx_buf, rx_buf + avail, rx_head - avail);
    rx_head -= avail;
    return (int)avail;
}

int of_net_broadcast(const void *data, size_t len)
{
    return net_send_bytes(data, len);
}

int of_net_send(const void *data, size_t len)
{
    return net_send_bytes(data, len);
}

int of_net_recv(void *data, size_t len)
{
    return of_net_recv_from(0, data, len);
}

int of_net_poll(void)
{
    if (net_state == OF_NET_DISCONNECTED) return 0;
    net_drain_rx();
    return (int)rx_head;
}

/* SBI vendor dispatcher for OF_EID_NET. Switches on FID. */
long of_net_dispatch(long fid, long a0, long a1, long a2)
{
    switch (fid) {
    case OF_NET_FID_HOST_START:   return of_net_host_start();
    case OF_NET_FID_JOIN:         return of_net_join();
    case OF_NET_FID_STOP:         of_net_stop(); return 0;
    case OF_NET_FID_STATUS:       return of_net_status();
    case OF_NET_FID_CLIENT_COUNT: return of_net_client_count();
    case OF_NET_FID_SEND_TO:      return of_net_send_to((int)a0, (const void *)a1, (size_t)a2);
    case OF_NET_FID_RECV_FROM:    return of_net_recv_from((int)a0, (void *)a1, (size_t)a2);
    case OF_NET_FID_BROADCAST:    return of_net_broadcast((const void *)a0, (size_t)a1);
    case OF_NET_FID_SEND:         return of_net_send((const void *)a0, (size_t)a1);
    case OF_NET_FID_RECV:         return of_net_recv((void *)a0, (size_t)a1);
    case OF_NET_FID_POLL:         return of_net_poll();
    default: return -2 /* OF_ERR_NOT_SUPPORTED */;
    }
}
