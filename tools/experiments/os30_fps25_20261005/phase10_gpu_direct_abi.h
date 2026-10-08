/* Private GPU-direct geometry ABI proposal, version GDR1. Word little-endian.
 * One CPU owner. Only a fabric reset may cancel accepted memory/GPU traffic. */
#ifndef PHASE10_GPU_DIRECT_ABI_H
#define PHASE10_GPU_DIRECT_ABI_H
#define GD_BASE 0x4b000000u
#define GD_ID 0x47445201u
#define GD_START_LOAD 1u
#define GD_ACK 2u
#define GD_START_PRECHECK 4u
#define GD_START_COMMIT 8u
#define GD_CANCEL_PRECHECK 16u
struct GdRegisters {
 const volatile uint32_t id,status;                         /*000,004*/
 volatile uint32_t control,source,shape,aspect;            /*008..014*/
 volatile uint32_t coefficient[3],ambient,diffuse,scales,lighting; /*018..030*/
 const volatile uint32_t reserved034,reserved038,reserved03c;
 volatile uint32_t invalidate;                            /*040: W1C raw validity*/
 volatile uint32_t command_source,run_config;              /*044,048*/
 const volatile uint32_t accepted_commands,live_triangles,reason; /*04c..054*/
 const volatile uint32_t reject_counts,final_stale;         /*058,05c*/
 volatile uint32_t draw_flags,initial_stale,prim_rgb,env_rgb;/*060..06c*/
 volatile uint32_t tile,linear_filter;                     /*070,074*/
 const volatile uint32_t emitted_words,emitted_vertices;   /*078,07c*/
 uint32_t reserved080[32];
 volatile uint32_t matrix[16];                            /*100..13c*/
 uint32_t reserved140[(0x400-0x140)/4];
 const volatile uint32_t raw[8][32];                       /*400..7fc*/
};
_Static_assert(__builtin_offsetof(struct GdRegisters,command_source)==0x44,"GDR1 RUN ABI");
_Static_assert(__builtin_offsetof(struct GdRegisters,draw_flags)==0x60,"GDR1 DRAW ABI");
_Static_assert(__builtin_offsetof(struct GdRegisters,matrix)==0x100,"GDR1 matrix ABI");
_Static_assert(__builtin_offsetof(struct GdRegisters,raw)==0x400,"GDR1 raw ABI");
/* status: busy0,completed1,success2,unsupported3,allinside4,readerror6:5,
 * matrix-config-error7,matrix-ready8; higherbits currently reserved.
 * shape=count[5:0]|dest[12:8]. run_config=cull[1:0]|budget[14:8];
 * cull:0 none,1 front,2 back,3 both. budget1..64 input triangles.
 * accepted_commands excludes first unsupported/nontriangle command.
 * reason:0 normal termination/budget,1invalid/unowned slot,2survivingclip,
 * 3Wrange/equality,4rawattributes,5AXIerror,6invaliddescriptor.
 * reject_counts=clip_rejected[7:0]|cull_rejected[15:8]|total_accepted_tri[23:16].
 * draw_flags=textured[0]|RGBsource[15:8]. RGB255white,4shade,3prim,5env,
 * 0/1/2/6zero;7LOD invalid. Prim/env Rlow8,G8,B16. Alpha/fog/material
 * eligibility remain CPU checks, and alpha is part of legacy surface state.
 * tile=uls_u16|(ult_u16<<16); linear_filter0/1. Integer UV offsets=tile*8.
 * Raw row0..4XYZW/inv,5signed16U/V,6RGBA,7clip8; synchronous onecycle read.
 * INVALIDATE and LOAD must not mutate rawslots while PRECHECK/COMMIT owns them.
 * PRECHECK never publishesGPUwords. CANCEL discards uncommitted survivors.
 * COMMIT success means everyword was accepted into ordered GPU transport,
 * not raster completion. The entire bounded COMMIT is reserved and staged
 * unpublished; publication is atomic on finalword. Any conversion/error abort
 * rewinds the unpublished append pointer, so whole-run CPU fallback is safe.
 */
#endif
