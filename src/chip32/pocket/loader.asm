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

// Core ID
constant core_id        = 0x0

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
                ld r0,#core_id
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
