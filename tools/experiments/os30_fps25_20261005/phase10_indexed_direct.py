#!/usr/bin/env python3
"""F11 one-pass indexed preparation directly into SDK-owned staging slices."""
import difflib
from pathlib import Path
from phase10_experiment import CAMP,INPUTS,sha,write
BASE=CAMP/'phase9/checks-state-f11';OUT=CAMP/'phase10/indexed-direct-v2'
def rep(t,a,b):
 assert t.count(a)==1,(a[:120],t.count(a));return t.replace(a,b)
def main():
 OUT.mkdir(exist_ok=False)
 originals={n:(BASE/n).read_text()for n in INPUTS}
 for n,t in originals.items():(OUT/n).write_text(t)
 p=OUT/'gfx_gpu.h';s=p.read_text();at=s.index('int gfx_gpu_geometry_enabled(void);')
 s=s[:at]+'''/* Private native producer owns this staging slice until commit/refill.
 * No backend/SDK operation may occur while packet has uncommitted words. */
#ifndef OF_PC
struct gfx_vc_stream {
    uint32_t *first, *packet, *limit;
    unsigned kick, loads, tris;
};
void gfx_gpu_vtx_cache_stream_begin(struct gfx_vc_stream *stream, uint8_t alpha);
void gfx_gpu_vtx_cache_stream_space(struct gfx_vc_stream *stream);
void gfx_gpu_vtx_cache_stream_commit(struct gfx_vc_stream *stream);
#endif
''' +s[at:];p.write_text(s)
 p=OUT/'gfx_gpu.c';s=p.read_text();a=s.index('void gfx_gpu_vtx_cache_batch(');body=s.index('    SM64_PERF_SCOPE(SM64_PERF_SEND);',a);end=s.index('    /* No state mutation occurs',body)
 begin=s[body:end]
 extra='''#ifndef OF_PC
/* Shared state setup occurs once at the first fully validated live command. */
void gfx_gpu_vtx_cache_stream_begin(struct gfx_vc_stream *stream, uint8_t alpha) {
'''+begin+'''    stream->first=stream->packet=stream->limit=NULL;
    stream->kick=g_vc_kick;stream->loads=stream->tris=0;
}
void gfx_gpu_vtx_cache_stream_space(struct gfx_vc_stream *stream) {
    if(stream->packet)_gpu_ring_commit((uint32_t)(stream->packet-stream->first));
    _gpu_stream_reserve_words(29);
    stream->first=stream->packet=_gpu_ring_claim();
    unsigned available=OF_GPU_COMMAND_STREAM_BATCH_WORDS-_gpu_cmd_words;
    const unsigned ring_available=_gpu_ring_free_known()/4u;
    if(available>ring_available)available=ring_available;
    stream->limit=stream->packet+available;
}
void gfx_gpu_vtx_cache_stream_commit(struct gfx_vc_stream *stream) {
    if(stream->packet)_gpu_ring_commit((uint32_t)(stream->packet-stream->first));
    stream->first=stream->packet=stream->limit=NULL;
    g_vc_words+=9u*stream->loads+2u*stream->tris;
    g_vc_loads+=stream->loads;g_vc_tris_cached+=stream->tris;
    stream->loads=stream->tris=0;
    g_vc_kick=stream->kick;
    if(stream->kick>=32u) {
        g_vc_kick=stream->kick=0;
        of_gpu_kick();
    }
}
#endif

'''
 s=s[:a]+extra+s[a:];p.write_text(s)
 p=OUT/'gfx_pc.c';s=p.read_text();a=s.index('static inline void gfx_cpu_prepare_clip(');end=s.index('\n/* Complete one fallback',a);helper=s[a:end]
 direct=helper.replace('gfx_cpu_prepare_clip(struct gfx_vc_prepared *d,','gfx_cpu_prepare_clip_words(uint32_t *d,')
 direct=rep(direct,'    d->x = qx; d->y = qy; d->w = qw;\n    d->u = qu; d->v = qv; d->rgb = rgb; d->depth = qd;','    d[0]=qx;d[1]=qy;d[2]=qw;d[3]=qu;d[4]=qv;d[5]=rgb;d[6]=qd;')
 s=s[:end]+'\n#ifndef OF_PC\n'+direct+'#endif\n'+s[end:]
 a=s.index('static __attribute__((noinline)) Gfx *gfx_cpu_indexed_run(');end=s.index('\n#endif\n\n#define C0',a);original=s[a:end];part=original
 part=rep(part,'    struct gfx_vc_prepared payload[32];\n    uint32_t jobs[64];','    struct gfx_vc_stream stream;')
 part=rep(part,'    uint32_t stale = 0, prepared = 0;','    uint32_t stale = 0;')
 part=rep(part,'            stale = vc_slot_stale;','''            stale = vc_slot_stale;
            if (buf_vbo_len) gfx_flush();
            gfx_gpu_vtx_cache_stream_begin(&stream,alpha);''')
 old='''            prepared |= stale & used;
            jobs[count++] = s[0] | (s[1] << 5) | (s[2] << 10) | (dirty << 15);
            stale &= ~used;'''
 new='''            /* All halves of this whole command passed rejection/guards.
             * Preserve the pre-triangle dirty bits, including duplicates. */
            if(!stream.packet || stream.limit-stream.packet<29)
                gfx_gpu_vtx_cache_stream_space(&stream);
            uint32_t *packet=stream.packet;
            uint32_t *prior_payload[3]={NULL,NULL,NULL};
            for(unsigned k=0;k<3;++k) {
                if(!(dirty&(1u<<k)))continue;
                *packet++=(0x56u<<24)|8u;
                *packet++=s[k];
                uint32_t *same=NULL;
                if(k>0 && s[k]==s[0])same=prior_payload[0];
                else if(k>1 && s[k]==s[1])same=prior_payload[1];
                prior_payload[k]=packet;
                if(same) {
                    /* Duplicate loads retain identical already-rounded bits. */
                    packet[0]=same[0];packet[1]=same[1];packet[2]=same[2];
                    packet[3]=same[3];packet[4]=same[4];packet[5]=same[5];packet[6]=same[6];
                } else {
                    const struct LoadedVertex *v=&rsp.loaded_vertices[s[k]];
                    float u=0.0f,tex_v=0.0f;
                    if(textured) {
                        u=(RSPV_TO_FLOAT(v->u)-uofs)/32.0f+bias;
                        tex_v=(RSPV_TO_FLOAT(v->v)-vofs)/32.0f+bias;
                    }
#if defined(__riscv)
                    __asm__ volatile ("" : "+f"(u), "+f"(tex_v));
#endif
                    uint16_t rgb=0xffff;
                    if(rgb_in>=0) {
                        struct RGBA tmp;
                        const struct RGBA *c=gfx_vc_resolve(comb->shader_input_mapping[0][rgb_in],v,v,&tmp);
                        rgb=((uint16_t)(c->r>>3)<<11)|((uint16_t)(c->g>>2)<<5)|(c->b>>3);
                    }
                    gfx_cpu_prepare_clip_words(packet,v,u,tex_v,rgb);
                }
                packet+=7;stream.loads++;
            }
            *packet++=(0x54u<<24)|1u;
            *packet++=s[0]|(s[1]<<5)|(s[2]<<10);
            stream.packet=packet;stream.tris++;stream.kick++;
            if(stream.kick>=32u)gfx_gpu_vtx_cache_stream_commit(&stream);
            count++;
            stale &= ~used;'''
 part=rep(part,old,new)
 start=part.index('    if (count) {',part.index('\nsubmit:'));stop=part.index('    if (fallback)',start)
 part=part[:start]+'''    if (count) {
        gfx_gpu_vtx_cache_stream_commit(&stream);
        vc_slot_stale=stale;
    }
'''+part[stop:]
 s=s[:a]+'#ifndef OF_PC\n'+part+'\n#else\n'+original+'\n#endif\n'+s[end:];p.write_text(s)
 patches=[]
 for n in INPUTS:
  t=(OUT/n).read_text()
  if t!=originals[n]:patches.extend(difflib.unified_diff(originals[n].splitlines(True),t.splitlines(True),fromfile='F11/'+n,tofile='indexed-direct-v2/'+n))
 (OUT/'candidate.patch').write_text(''.join(patches))
 write(OUT/'source.json',dict(parent_elf_sha256=sha(BASE/'app.elf'),source_sha256={n:sha(OUT/n)for n in INPUTS},helper_sha256=sha(Path(__file__)),scope=__doc__,qualified_for_deployment=False));print(OUT)
if __name__=='__main__':main()
