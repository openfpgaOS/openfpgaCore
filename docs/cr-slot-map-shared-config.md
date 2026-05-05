# Change request: teach `datatable_entry_for_slot` about Shared Config (slot 8)

## Status

**Open.**  Reproducible on the SDK's `da` core after commit
`a049904` (sdk: app modernization sweep + analogcfg + …) added the
"Shared Config" entry at slot id 8, array position 8 in
`dist/sdk/Cores/ThinkElastic.openfpgaOS/data.json`.

Symptom: opening **Save A** from the launcher reports

> `File ID[9] too large`

and Save B / Save C / … all read back the *previous* save's contents
(off-by-one) because the OS' hardcoded slot-id → datatable-entry map is
out of sync with the new data.json layout.

## Problem

`firmware/os/targets/pocket/file.c:286-304` —

```c
static int datatable_entry_for_slot(uint32_t slot_id, uint32_t *entry_out) {
    /* APF's datatable is indexed by array position in data.json, while
     * DS_CMD_READ/GETFILE use the slot `id` field. openfpgaOS reserves
     * ids 10-19 for the ten nonvolatile save slots, which are appended
     * after entries 0-7 in data.json; ids 8 and 9 are intentionally
     * unused in current templates. */
    if (slot_id < 8) {
        *entry_out = slot_id;
        return 0;
    }

    if (slot_id >= 10 &&
        slot_id < 10 + (uint32_t)OF_TARGET_SAVE_MAX_SLOTS) {
        *entry_out = 8 + (slot_id - 10);
        return 0;
    }

    return -1;
}
```

Two things are wrong with this once a real entry lands at id 8:

1. `slot_id < 8` no longer covers the eight pre-save entries — the new
   data.json has **nine** entries before the saves (ids 0-7 plus the
   "Shared Config" at id 8).  A request for slot 8 falls through to
   `return -1` and the caller treats it as a missing slot.
2. The save mapping `entry = 8 + (slot_id - 10)` is off by one because
   "Shared Config" pushed every save's array position up by one.  Save
   id 10 is now at entry 9, not entry 8 — and so on.

The "File ID[9]" wording comes from the launcher / chip32 loader
seeing entry 9 (= our first save) reported with the wrong size /
flags, then bailing because the metadata it reads is the previous
slot's.

The comment is also stale: "ids 8 and 9 are intentionally unused" is
no longer true.

## data.json layout (current, post `a049904`)

| Array position | id | name           |
|---------------:|---:|----------------|
| 0              | 0  | Game           |
| 1              | 1  | OS Binary      |
| 2              | 2  | Application    |
| 3              | 3  | Data 1         |
| 4              | 4  | Data 2         |
| 5              | 5  | Data 3         |
| 6              | 6  | Data 4         |
| 7              | 7  | Sound Bank     |
| 8              | 8  | **Shared Config** ← new |
| 9              | 10 | Save 0 (Save A) |
| 10             | 11 | Save 1          |
| …              | …  | …              |
| 18             | 19 | Save 9          |

ID 9 remains a deliberate gap (so adding a single new pre-save slot
later doesn't move every save again).

## Fix

Replace `datatable_entry_for_slot()` with the layout above:

```c
static int datatable_entry_for_slot(uint32_t slot_id, uint32_t *entry_out) {
    /* APF's datatable is indexed by array position in data.json, while
     * DS_CMD_READ/GETFILE use the slot `id` field.  Current layout:
     *   ids 0-7   → entries 0-7   (game, os, app, data 1-4, soundbank)
     *   id  8     → entry  8      (shared config)
     *   id  9     → reserved (gap; not present in data.json)
     *   ids 10-19 → entries 9-18  (ten nonvolatile save slots)
     * If you add or remove a pre-save slot in data.json, this map MUST
     * be updated in lockstep — the relationship is contractual and
     * APF does not expose the id field at runtime to let us derive it. */
    if (slot_id <= 8) {
        *entry_out = slot_id;
        return 0;
    }

    if (slot_id >= 10 &&
        slot_id < 10 + (uint32_t)OF_TARGET_SAVE_MAX_SLOTS) {
        *entry_out = 9 + (slot_id - 10);
        return 0;
    }

    return -1;
}
```

Three things to highlight:

1. **`slot_id <= 8`, not `< 8`.**  Slot 8 lives at array position 8,
   so the identity branch extends one slot further.  Slot 9 is
   intentionally left to fall through to `return -1`.
2. **Save base entry is now 9, not 8.**  Every save's array position
   shifted by +1 when "Shared Config" was inserted.
3. **Comment rewritten to be a maintenance contract.**  Anyone
   touching `data.json` needs to know they have one place to update
   on the OS side.  A future CR will replace this with a build-time
   generator, but for now the rule lives in the comment.

## Verification

After the fix, on the SDK's current core:

- Launcher → **Save A**: opens cleanly, no "File ID[9] too large".
- `slotdemo` enumerates 19 slots (ids 0-8, 10-19) with the correct
  names from `of_file_get_name()`.
- `analogcfg` continues to read/write its 32-byte cfg at slot 8
  (`fopen("slot:8", ...)`).
- Save A through Save J round-trip distinct contents — the off-by-one
  is gone.

Quick smoke test: `slotdemo` printing `slot:10 size=N name="…"` should
show the right size (`0x40000`) and name (`Save 0`), not the
`Shared Config` size (`0x1000`).

## Non-fix (for the record)

We considered moving the `Shared Config` entry to the *end* of
data.json so the firmware mapping wouldn't need updating, but that's
a hack: the next pre-save slot we add hits the same trap and shifts
the saves again.  Better to fix the map now, even though it stays
hand-maintained until the build-time generator lands.

## Estimated effort

- Patch + build: **5 min** (≈10 lines of C, 1 file).
- Re-flash + verify on hardware: **10 min**.
- Total: **15 min**.

## Follow-up (separate CR, not this one)

Once this lands, a future CR should auto-generate this table from
`data.json` at build time so that hand-editing the firmware in lockstep
with data.json stops being a manual step.  Sketch: a small Python
script that emits `firmware/os/targets/pocket/slot_map.h` with the
`slot_id_to_entry[]` array plus `SAVE_SLOT_ID_BASE` /
`OF_TARGET_SAVE_MAX_SLOTS` derived from the JSON.  Tracked separately
to keep this CR a 15-minute fix.
