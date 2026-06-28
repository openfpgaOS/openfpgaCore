architecture chip32.vm
output "loader.bin", create

// openfpgaOS Chip32 Loader
//
// Handles bitstream loading and handoff to the FPGA core. Save files are
// ordinary APF nonvolatile data slots; the bridge auto-loads them to the
// CRAM0 save window from data.json.
//
// Filename→slot mapping is done by apps via of_file_slot_register().
//
// R0  = data slot ID selected by user (from instance JSON)
// R13 = persists across reloads (bitfield for state tracking)

// Core IDs — must match the order of core.json "cores" list.
constant core_os25      = 0x0    // default 2.5D / span-group bitstream
constant core_os30      = 0x1    // hardware vertex-triangle bitstream

// ── Per-instance variant selection ──────────────────────────────────
// The chip32 loader runs on the Pocket host BEFORE the FPGA core is
// configured, so it cannot touch PSRAM/the bridge (0x2xxxxxxx CRAM0 lives
// behind the not-yet-loaded core's controller — chicken-and-egg).  The only
// pre-core way to see a data slot's bytes is to `open` it and `read` it into
// chip32's own 8 KB RAM, exactly as in Analogue's core-example-basicchip32
// (`open r3,r0 / read rambuf,len`).  We read the app's os.ini (slot 2) into a
// scratch buffer and scan it for the marker "=os30": found → core 1 (os30),
// else core 0 (os25, default).  Anchoring on '=' (a value assignment, not bare
// "os30") keeps prose — comments, ARGS, ELF names — from false-triggering.
constant ini_slot       = 0x2
// Scratch buffer in chip32's 8 KB RAM (0x0000-0x1FFF).  The program + strings
// occupy only the low ~256 B, so 0x0800 is clear of code, and an os.ini (a few
// hundred bytes) read in at 0x0800 stays well inside the 8 KB RAM.  read caps a
// single transfer at 4 KB; an os.ini is far smaller, so we read the whole file
// in one go (no length clamp — that avoids any carry-flag polarity assumption,
// and a pathological >4 KB .ini simply fails the read and defaults to os25).
constant rambuf         = 0x0800

// ASCII for the "=os30" marker scanned out of the .ini.
constant ch_eq          = 0x3D
constant ch_o           = 0x6F
constant ch_s           = 0x73
constant ch_3           = 0x33
constant ch_0           = 0x30

// Host commands
constant host_reset     = 0x4000
constant host_init      = 0x4002

// R13 bitfield
variable bit_coreloaded = 0x1
variable bit_initdone   = 0x2

// ====================================================================
// Error handler (address 0x0000)
// ====================================================================
                jp error

// ====================================================================
// Entry point (address 0x0002)
// ====================================================================
start:
                // --- Load bitstream (first boot only) ---
                bit r13,#bit_coreloaded
                jp nz,skip_core
                // Open the os.ini (slot 2).  chip32 `open Rx,Ry`: Rx = slot id
                // (INPUT, left unchanged), Ry = file length (output), ZERO FLAG
                // = success (set = opened).  Rx is NOT a status return, so we
                // branch on the flag — a `cmp r0,#0` would test the slot id
                // (still 2 → always nz) and wrongly default every app to os25.
                ld r0,#ini_slot
                open r0,r1                  // r1 = size; zero flag = opened OK
                jp nz,pick_os25             // open failed (no .ini) → default os25
                ld r7,r1                    // r7 = byte count (for the scan end)
                ld r0,#rambuf               // r0 = destination in chip32 RAM
                read r0,r1                  // copy the whole .ini into rambuf
                close                       // (close preserves the read's flag)
                jp nz,pick_os25             // read error (.ini > 4 KB) → default os25
                ld r4,#rambuf               // r4 = scan pointer
                ld r5,#rambuf
                add r5,r7                   // r5 = end = rambuf + bytecount
scan:
                cmp r4,r5
                jp z,pick_os25              // reached EOF, no match → default os25
                ld.b r2,(r4)
                cmp r2,#ch_eq               // '=' (anchor) ?
                jp nz,scan_next
                ld r6,r4                    // peek ahead without losing r4
                add r6,#1
                ld.b r2,(r6)
                cmp r2,#ch_o                // 'o' ?
                jp nz,scan_next
                add r6,#1
                ld.b r2,(r6)
                cmp r2,#ch_s                // 's' ?
                jp nz,scan_next
                add r6,#1
                ld.b r2,(r6)
                cmp r2,#ch_3                // '3' ?
                jp nz,scan_next
                add r6,#1
                ld.b r2,(r6)
                cmp r2,#ch_0                // '0' ?
                jp nz,scan_next
                // matched "=os30"
                ld r0,#core_os30
                jp pick_core
scan_next:
                add r4,#1
                jp scan
pick_os25:
                ld r0,#core_os25
pick_core:
                core r0
                or r13,#bit_coreloaded

skip_core:
                // --- On reload, reset the core ---
                bit r13,#bit_initdone
                jp z,first_start
                ld r0,#host_reset
                host r0,r0
                jp start_core

// ====================================================================
// Hand off to core
// ====================================================================
first_start:
                or r13,#bit_initdone

start_core:
                ld r0,#host_init
                host r0,r0
                exit 0

// ====================================================================
// Error handler
// ====================================================================
error:
                ld r0,#errmsg
                printf r0
                err r0,r1
                hex.b r0
                ld r0,#err_at
                printf r0
                hex.w r1
                ld r0,#err_nl
                printf r0
                exit 1

errmsg:
                db "Loader error 0x",0
err_at:
                db " at 0x",0
err_nl:
                db 10,0
