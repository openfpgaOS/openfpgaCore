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

// Per-instance variant selection by reading the app's os.ini directly.
// data.json gives the OS Config slot (id 2) a fixed load address, so the
// bridge places the .ini text where the VM can byte-scan it BEFORE the FPGA is
// configured (the chip32 loader runs on the Pocket host, ahead of the core).
// An app that needs the vertex-triangle bitstream puts a line `VARIANT=os30`
// in its .ini; the loader scans for the marker "=os30" and loads core 1, else
// it defaults to core 0 (os25).  Anchoring on the '=' (a value assignment, not
// bare "os30") keeps prose — comments, ARGS, ELF names mentioning os30 — from
// false-triggering the raw byte scan.
constant ini_slot       = 0x2
constant ini_addr       = 0x10000000   // must match data.json slot 2 "address"

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
                // Open the os.ini (slot 2) to bound the scan to its real size,
                // so stray memory past EOF can't spuriously match "os30".
                ld r0,#ini_slot
                open r0,r1                  // r0 = status, r1 = size
                cmp r0,#0000
                jp nz,pick_os25             // no .ini → default os25
                ld r4,#ini_addr             // r4 = scan pointer
                ld r5,r1
                add r5,r4                   // r5 = end = ini_addr + size
scan:
                cmp r4,r5
                jp z,scan_done              // reached EOF → default os25
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
                close
                ld r0,#core_os30
                jp pick_core
scan_next:
                add r4,#1
                jp scan
scan_done:
                close
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
