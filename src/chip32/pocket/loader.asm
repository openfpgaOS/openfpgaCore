architecture chip32.vm
output "loader.bin", create

// openfpgaOS Chip32 Loader — generic single-pinned-bitstream loader
//
// chip32 runtime `core`-switching does NOT take effect on the Pocket: a core
// only ever loads its default/first cores[] entry.  So every openfpgaOS core
// pins exactly ONE bitstream as its sole cores[] entry, and this loader simply
// loads `core 0` — that single entry, whatever it is.  Shared by ANY such core:
// the os25 demo core (os25.rbf_r) and the os30/3D core (os30.rbf_r) both use
// this identical loader.bin.

constant core_pinned    = 0x0    // the single cores[] entry (core 0), whatever it is
constant host_reset     = 0x4000
constant host_init      = 0x4002
variable bit_coreloaded = 0x1
variable bit_initdone   = 0x2

                jp error
start:
                bit r13,#bit_coreloaded
                jp nz,skip_core
                ld r0,#core_pinned
                core r0
                or r13,#bit_coreloaded
skip_core:
                bit r13,#bit_initdone
                jp z,first_start
                ld r0,#host_reset
                host r0,r0
                jp start_core
first_start:
                or r13,#bit_initdone
start_core:
                ld r0,#host_init
                host r0,r0
                exit 0
error:
                ld r0,#errmsg
                printf r0
                err r0,r1
                hex.b r0
                exit 1
errmsg:
                db "Loader err 0x",0
