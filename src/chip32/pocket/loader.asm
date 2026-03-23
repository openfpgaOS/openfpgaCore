architecture chip32.vm
output "loader.bin", create

// openfpgaOS Chip32 Loader
//
// Handles bitstream loading, save file initialization, and
// datatable size management.
//
// Filename→slot mapping is done by apps via of_file_slot_register().
//
// R0  = data slot ID selected by user (from instance JSON)
// R13 = persists across reloads (bitfield for state tracking)

// Core ID
constant core_id        = 0x0

// Host commands
constant host_reset     = 0x4000
constant host_run       = 0x4001
constant host_init      = 0x4002

// R13 bitfield
variable bit_coreloaded = 0x1
variable bit_initdone   = 0x2

// CRAM1 bridge addresses
constant cram1_base     = 0x30000000
constant slot_stride    = 0x40000       // 256KB physical slot spacing
constant slot_max_size  = 0x40000       // 256KB max per slot

// FPGA save-size bridge addresses
constant save_size_all  = 0xF0000000
constant save_size_base = 0xF0000010

// Fill pattern
constant fill_word      = 0xFFFFFFFF

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
                jp z,init_sizes
                ld r0,#host_reset
                host r0,r0

// ====================================================================
// Initialize all datatable sizes to 0
// ====================================================================
init_sizes:
                ld r4,#save_size_all
                ld r5,#0
                pmpw r4,r5

// ====================================================================
// Load saves
// ====================================================================
                ld r6,#10
                ld r7,#cram1_base
                ld r12,#0

save_loop:
                ld r0,r6
                open r0,r1
                jp nz,save_missing

                close
                cmp r1,#5
                jp z,save_missing
                cmp r1,#0
                jp z,save_missing

                ld r0,r6
                loadf r0

                ld r4,#save_size_base
                ld r5,r12
                asl r5,#2
                add r4,r5
                ld r1,#slot_max_size
                pmpw r4,r1

                jp save_next

save_missing:
                ld r8,r7
                ld r10,#0x54504D45
                pmpw r8,r10
                add r8,#4
                ld r10,#0xFFFFFF59
                pmpw r8,r10
                add r8,#4

                ld r9,#slot_max_size
                sub r9,#8
                ld r10,#fill_word
fill_loop:
                pmpw r8,r10
                add r8,#4
                sub r9,#4
                jp nz,fill_loop

                ld r4,#save_size_base
                ld r5,r12
                asl r5,#2
                add r4,r5
                ld r1,#5
                pmpw r4,r1

save_next:
                add r6,#1
                add r7,#slot_stride
                add r12,#1
                cmp r6,#20
                jp nz,save_loop

// ====================================================================
// Hand off to core
// ====================================================================
                bit r13,#bit_initdone
                jp nz,reload

                or r13,#bit_initdone
                ld r0,#host_init
                host r0,r0
                exit 0

reload:
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
