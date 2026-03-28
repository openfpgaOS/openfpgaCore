/*
 * Cycle-accurate analytical model of DMA copy path.
 *
 * Traces one 8-word block through:
 *   DMA Engine → AXI Arbiter → AXI SDRAM Slave → io_sdram
 *
 * Timing from io_sdram.v @ 100 MHz:
 *   CAS=3, tRCD=2, tRP=2, tWR=2, BL=2 (16-bit bus)
 *   Row-hit writes: skip ACT+tRCD
 */

#include <stdio.h>
#include <stdint.h>

int main(void) {
    printf("=== DMA 8-word Block Cycle Trace ===\n");
    printf("(src and dst in different SDRAM rows)\n\n");

    int c = 0;

    /* ---- READ PHASE (burst read 8 words) ---- */
    printf("READ PHASE:\n");

    printf("  [%3d] DMA: S_CALC → compute burst_len=7\n", c); c++;
    printf("  [%3d] DMA: S_RD_ADDR → assert arvalid\n", c); c++;
    printf("  [%3d] ARB: ST_IDLE → grant M1, ST_RD (registered)\n", c);
    printf("        SLV: S_IDLE sees arvalid, accept AR, early sdram_rd\n", c);
    printf("        SDRAM: IDLE → WRITE_ACT, row precharge if needed\n", c); c++;

    printf("  [%3d] SLV: S_RD_CMD, sdram accepted → S_RD_DAT\n", c);
    printf("        SDRAM: ACT row (1 cycle)\n", c);
    printf("        DMA: sees arready → S_RD_DATA\n", c); c++;

    printf("  [%3d] SDRAM: tRCD wait (1 of 2)\n", c); c++;
    printf("  [%3d] SDRAM: tRCD wait (2 of 2) → READ cmd #1\n", c); c++;
    printf("  [%3d] SDRAM: READ #1 BL2 beat 2, issue READ #2\n", c); c++;

    /* Remaining 6 READ commands (2 cycles each) */
    for (int i = 2; i <= 7; i++) {
        printf("  [%3d] SDRAM: READ #%d cmd\n", c, i); c++;
        printf("  [%3d] SDRAM: READ #%d BL2", c, i);
        if (i < 7) printf(", issue READ #%d", i+1);
        printf("\n"); c++;
    }

    /* CAS pipeline: first data arrives CAS+1=4 cycles after first READ cmd */
    int first_read_cmd = 5;  /* cycle when first READ was issued */
    int first_data = first_read_cmd + 4;  /* CAS=3 + 1 input reg */

    printf("  [%3d] SDRAM: CAS drain begins\n", c); c++;
    printf("  ...\n");

    /* Data arrives every 2 cycles (matching READ cmd rate) */
    printf("\n  Data arrival (rdata_valid pulses):\n");
    for (int i = 0; i < 8; i++) {
        int data_cycle = first_data + i * 2;
        printf("    Word %d: cycle %d%s\n", i, data_cycle,
               i == 7 ? " (rlast)" : "");
    }

    int read_end = first_data + 7 * 2 + 1;  /* cycle after last data + arbiter release */
    printf("\n  [%3d] ARB: rlast → ST_IDLE\n", read_end);
    printf("  Read phase total: %d cycles\n\n", read_end - 0);
    c = read_end + 1;

    /* ---- WRITE PHASE (8 individual words) ---- */
    printf("WRITE PHASE:\n");
    int write_start = c;

    printf("  [%3d] DMA: S_WR_ADDR → assert awvalid\n", c); c++;
    printf("  [%3d] ARB: ST_IDLE → grant M1, ST_WR (registered)\n", c); c++;
    printf("  [%3d] SLV: S_IDLE sees awvalid, accept AW → S_WR_NEXT\n", c);
    printf("        DMA: sees awready → S_WR_DATA, assert wvalid\n", c); c++;

    /* Write word 0 (cold — different row from read) */
    printf("  [%3d] SLV: S_WR_NEXT, accept W → S_WR_CMD\n", c); c++;
    printf("  [%3d] SLV: S_WR_CMD, !busy → sdram_wr, cmd_issued\n", c);
    printf("        SDRAM: precharge old row (2 cycles)\n", c); c++;

    /* SDRAM cold write: precharge(2) + ACT(1) + tRCD(2) + WR(1) + BL2(1) + tWR(3) = 10 */
    printf("  [%3d] SLV: S_WR_CMD, hold wr, accepted → S_WR_DON\n", c); c++;
    printf("  [%3d-%3d] SLV: S_WR_DON, wait !busy (SDRAM: ACT+tRCD+WR+BL2+tWR)\n",
           c, c + 7);
    int sdram_cold_wr = 8;  /* busy cycles for cold write after accepted */
    c += sdram_cold_wr;
    printf("  [%3d] SLV: !busy → beat_count++, S_WR_NEXT\n", c); c++;

    int word0_cycles = c - write_start - 4;  /* cycles for word 0 through slave */

    /* Words 1-7 (row hit) */
    int row_hit_wr = 4;  /* busy cycles for row-hit write */
    for (int i = 1; i < 8; i++) {
        int ws = c;
        /* SLV_WR_NEXT: 1 cycle */
        c++;
        /* SLV_WR_CMD: issue wr (1), accepted (1) */
        c += 2;
        /* SLV_WR_DON: wait !busy */
        c += row_hit_wr;
        /* SLV: next */
        c++;
        if (i == 1 || i == 7)
            printf("  [%3d-%3d] Word %d (row hit): %d cycles\n", ws, c-1, i, c - ws);
        else if (i == 2)
            printf("  ... (words 2-6 similar) ...\n");
    }

    printf("  [%3d] SLV: last word done → bvalid\n", c);
    printf("  [%3d] ARB: bvalid → ST_IDLE\n", c); c++;
    printf("  [%3d] DMA: S_WR_RESP, bvalid → S_IDLE (block done)\n", c); c++;

    int write_cycles = c - write_start;
    int total = c;

    printf("\n=== SUMMARY ===\n");
    printf("Read phase:      %d cycles\n", read_end + 1);
    printf("Write phase:     %d cycles\n", write_cycles);
    printf("  Word 0 (cold): %d cycles in slave\n", word0_cycles);
    printf("  Words 1-7:     %d cycles each (row hit)\n", 1 + 2 + row_hit_wr + 1);
    printf("Total per block: %d cycles for 32 bytes\n\n", total);

    /* Account for row switch overhead between consecutive blocks */
    /* Each block: read from src row, write to dst row.
     * Next block read needs to precharge dst row and activate src row.
     * That's tRP(2) + ACT(1) = 3 extra cycles folded into next read. */
    int row_switch = 3;
    int adjusted = total + row_switch;

    printf("With row switch between blocks: %d cycles\n\n", adjusted);

    double bytes_per_block = 32.0;
    double freq_mhz = 100.0;

    printf("Throughput:  %.1f bytes / %d cycles * %.0f MHz = %.1f MB/s\n",
           bytes_per_block, adjusted, freq_mhz,
           bytes_per_block / adjusted * freq_mhz);

    int blocks_1mb = 1048576 / 32;
    double cycles_1mb = (double)blocks_1mb * adjusted;
    double time_ms = cycles_1mb / (freq_mhz * 1000.0);
    double speed_mbs = 1048576.0 / (time_ms / 1000.0) / 1e6;

    printf("\nFor 1 MB:\n");
    printf("  Blocks:   %d\n", blocks_1mb);
    printf("  Cycles:   %.0f\n", cycles_1mb);
    printf("  Time:     %.2f ms\n", time_ms);
    printf("  Speed:    %.1f MB/s\n", speed_mbs);

    /* Add cache flush overhead */
    int flush_reads = 512;  /* 256 sets × 2 ways */
    /* Each flush read is a single-word SDRAM read: ~7 cycles average */
    int flush_cycles = flush_reads * 7 * 2;  /* ×2 for src and dst flush */
    double flush_ms = flush_cycles / (freq_mhz * 1000.0);

    printf("\nCache flush overhead: %d cycles (%.2f ms)\n", flush_cycles, flush_ms);
    double total_ms = time_ms + flush_ms;
    double total_speed = 1048576.0 / (total_ms / 1000.0) / 1e6;
    printf("Total with flush:    %.2f ms → %.1f MB/s\n", total_ms, total_speed);

    /* Auto-refresh overhead */
    int refreshes = (int)(cycles_1mb / 512);
    int refresh_cycles = refreshes * 10;  /* ~10 cycles per refresh */
    printf("\nAuto-refresh:  ~%d refreshes × 10 cycles = %d cycles (+%.1f%%)\n",
           refreshes, refresh_cycles, refresh_cycles / cycles_1mb * 100.0);

    /* What burst writes would give us */
    printf("\n=== WHAT-IF: Burst Writes ===\n");
    int burst_wr_phase = 3 + 2 + 8 + 16 + 3;  /* AW handshake + ACT+tRCD + 8 words streamed + tWR */
    int burst_total = (read_end + 1) + 2 + burst_wr_phase;
    printf("Write 8 words burst:  ~%d cycles (vs %d single-word)\n", burst_wr_phase, write_cycles);
    printf("Block total:          %d cycles (vs %d)\n", burst_total, total);
    printf("Throughput:           %.1f MB/s\n",
           bytes_per_block / burst_total * freq_mhz);

    return 0;
}
