# AJ03 cached geometry groups

Experimental capability word0x20 is `0x414a3033`. All AJ02 MMIO, ordered repair, priority lease, consumer-reset and true completed-sequence semantics remain. Single JOB remains supported.

A complete context is `0x6300001d`, base0, then exactly28 context words. It must immediately precede either old JOB `0x6400000b` or GROUP `0x65000007`.

GROUP seven words: first sequence, descriptor PSRAM byte offset, member count, flags/RGB, U offset, V offset, constant alpha. Count is1..32. Sequence is nonzero and first+count-1 cannot overflow. Descriptor table is8-byte aligned and wholly inside PSRAM [0,0x01000000).

Each descriptor is six32-bit words (24-byte stride): vertex PSRAM byte offset, original VTX word0, TRI PSRAM byte offset, command count/cull encoding, original CPU vertex pointer, original CPU TRI pointer. Existing individual JOB checks still apply. Shared material fields retain their existing ranges. The frontend's internal unchanged JOB interface encodes GROUP by control0x80000000, vertex=table offset, VTXword0=count; all other internal unused fields are zero.

A group owns one immutable context backup in existing attribute RAM words96..123. Legacy matrix/scalar writes update the shared compute context but do not overwrite this backup. Restore locks out legacy acquisition once begun; a pending acquire wins before restoration begins. Each member retains existing exact preflight, snapshot, repair and output ordering. Snapshot commit allows CPU priority raw ownership during old output. New member execution waits for old output and any legacy lease. True completed sequence advances only after each member's final ordered output; frontend GROUP completion occurs after the final member. Source table, vertex data and TRI bytes remain immutable through the last sequence, independently of priority grants.

Only individually admitted, dead-slot-proven tasks may be grouped. Common context and material must be exact. Cache binds source generation and repair-hint epoch; new hints affect future submission, never mutate an active group. No canonical handback or broader admission is implied.

Fabric reset clears everything with the AXI fabric. Consumer reset cancels group/partial context, invalidates repair tokens, discards coupled GPU output, drains accepted descriptor/raw reads, and preserves a granted legacy lease and its raw DMA. Descriptor faults are sticky protocol faults; no partial descriptor is launched. Existing pure repair remains per member, exposing its original source pointers and common context. END cannot precede its last accepted repair word.
