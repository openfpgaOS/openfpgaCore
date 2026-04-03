# File I/O 64KB Read Hang — ROOT CAUSE FOUND AND FIXED

## Root Cause

The PSRAM1 mux gave bridge absolute priority over the CDC. When the 
Pocket's APF host read CRAM1 during a CPU memcpy, `bridge_cram1_active`
blocked the CDC's reads. The CDC and bridge both waited for
`psram1_rdata_valid` — whoever's response came first was captured by
both, causing data corruption and hangs.

## The Fix (verified in Verilator simulation)

Three changes to the PSRAM1 mux:

1. **Response ownership tracking**: A `psram1_rd_owner` register locks
   to the requester (CDC or bridge) when `psram1_rd` fires. It stays
   locked until `rdata_valid` returns. `rdata_valid` is filtered to
   only the owner — preventing response theft.

2. **Inflight guard**: `psram1_rd` is blocked while a read is already
   inflight (`psram1_rd_inflight`). No overlapping requests.

3. **Combinatorial `bridge_requesting`**: Must be a wire (not reg) so
   the CDC sees it on the same cycle and doesn't issue a read that the
   mux will block.

## Verilator Test Results

```
=== Test 1: 512 rapid CPU reads (simulates 32KB D-cache fill) ===
Test 1 complete: 512 reads, 0 errors, 109 bridge reads injected
=== Test 2: 8192 rapid CPU reads (simulates 32KB uncached memcpy) ===
Test 2 complete: 8192 reads, 0 errors, 1933 bridge reads injected
=== ALL TESTS PASSED ===
```
