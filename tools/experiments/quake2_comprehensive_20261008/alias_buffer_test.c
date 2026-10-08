/* Execute the actual GL1 buffer owner with recorder GL entry points. */
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include "src/client/refresh/gl1/gl1_buffer.c"

glconfig_t gl_config;
glstate_t gl_state;
refdef_t r_newrefdef;
float gldepthmin=0.0f,gldepthmax=1.0f;
static cvar_t hand,gunfov,overbright;
cvar_t *gl_lefthand=&hand,*r_gunfov=&gunfov,*gl1_overbrightbits=&overbright;
static const void *uv_pointer,*vertex_pointer,*colour_pointer;
static int uv_stride,tex_array,blend_enabled,texture_enabled=1;
static unsigned draws,world_draws,pushes,pops,perspectives;
static float depth_low,depth_high=1;
static GLenum cull=GL_FRONT;
void Com_DPrintf(const char *format, ...) {(void)format;}
static struct {
    const void *indices,*uv; int count,stride,textured,blend,flags;
    float depth_low,depth_high;
} seen[64];

static void cap_enable(GLenum cap) { if(cap==GL_BLEND)blend_enabled=1; if(cap==GL_TEXTURE_2D)texture_enabled=1; }
static void cap_disable(GLenum cap) { if(cap==GL_BLEND)blend_enabled=0; if(cap==GL_TEXTURE_2D)texture_enabled=0; }
static void array_enable(GLenum cap) {if(cap==GL_TEXTURE_COORD_ARRAY)tex_array=1;}
static void array_disable(GLenum cap) {if(cap==GL_TEXTURE_COORD_ARRAY)tex_array=0;}
static void no_enum(GLenum e) {(void)e;}
static void push(void) {pushes++;}
static void pop(void) {pops++;}
static void nothing(void) {}
static void scale(GLfloat x,GLfloat y,GLfloat z) {(void)x;(void)y;(void)z;}
static void colour(GLfloat r,GLfloat g,GLfloat b,GLfloat a) {(void)r;(void)g;(void)b;(void)a;}
static void tex_env(GLenum a,GLenum b,GLint c) {(void)a;(void)b;(void)c;}
static void depth(GLfloat low,GLfloat high) {depth_low=low;depth_high=high;}
static void cull_face(GLenum face) {cull=face;}
static void vertices(GLint n,GLenum type,GLsizei stride,const void *p) {
    assert(n==2||n==3);assert(type==GL_FLOAT&&stride==0);vertex_pointer=p;
}
static void texcoords(GLint n,GLenum type,GLsizei stride,const void *p) {
    assert(n==2&&type==GL_FLOAT);uv_pointer=p;uv_stride=stride;
}
static void colours(GLint n,GLenum type,GLsizei stride,const void *p) {
    assert(n==4&&type==GL_UNSIGNED_BYTE&&stride==0);colour_pointer=p;
}
static void draw(GLenum mode,GLsizei count,GLenum type,const void *p) {
    assert(draws<64&&mode==GL_TRIANGLES&&type==GL_UNSIGNED_SHORT&&count>0);
    assert(vertex_pointer==gl_buf.vtx);
    seen[draws].indices=p;seen[draws].count=count;seen[draws].uv=uv_pointer;
    seen[draws].stride=uv_stride;seen[draws].textured=tex_array;
    seen[draws].blend=blend_enabled;seen[draws].flags=gl_buf.flags;
    seen[draws].depth_low=depth_low;seen[draws].depth_high=depth_high;
    const unsigned short *indices=p;
    for(int i=0;i<count;i++) {
        assert(indices[i]<vtx_ptr);
        if(tex_array) {
            const float *uv=(const float *)((const unsigned char *)uv_pointer+
                indices[i]*(uv_stride?uv_stride:2*sizeof(float)));
            /* Force live reads so ASan observes stale or undersized views. */
            volatile float live=uv[0]+uv[1];(void)live;
        }
    }
    if(gl_buf.type==buf_alias)assert(colour_pointer==gl_buf.clr);
    draws++;
}
void R_EnableMultitexture(qboolean e) {(void)e;}
void R_TexEnv(GLenum e) {(void)e;}
qboolean R_Bind(int texture) {(void)texture;return true;}
void R_MBind(GLenum target,int texture) {(void)target;(void)texture;}
void R_SetPerspective(GLdouble fovy) {(void)fovy;perspectives++;}
int OFGL_WorldPolysEnabled(void) {return 1;}
void OFGL_DrawWorldPolys(ofgl_world_poly_t *const *polys,int count) {
    assert(count==1&&polys[0]);world_draws++;
}

static of_alias_mesh_t *make_mesh(void) {
    size_t bytes=sizeof(of_alias_mesh_t)+4*sizeof(of_alias_vertex_t)+6*sizeof(unsigned short);
    of_alias_mesh_t *m=calloc(1,bytes);assert(m);m->numverts=4;m->numindices=6;
    for(int i=0;i<4;i++){m->vertices[i].xyz=i&1;m->vertices[i].s=(float)i+.25f;m->vertices[i].t=(float)-i-.5f;}
    const unsigned short idx[]={0,1,2,0,2,3};
    memcpy(m->vertices+4,idx,sizeof(idx));return m;
}
static void set_alias(int flags) {
    R_ResetGLBuffer();gl_buf.type=buf_alias;gl_buf.flags=flags;gl_buf.vt=gl_buf.tx=gl_buf.cl=0;
    tex_array=0;blend_enabled=0;texture_enabled=1;depth_low=0;depth_high=1;
}
static void prepare_data(void) {
    for(unsigned i=0;i<12;i++)gl_buf.vtx[i]=(float)i;
    for(unsigned i=0;i<8;i++)gl_buf.tex[0][i]=(float)i;
    for(unsigned i=0;i<16;i++)gl_buf.clr[i]=(byte)i;
    gl_buf.vt=12;gl_buf.cl=16;
}

int main(void) {
    glad_glEnable=cap_enable;glad_glDisable=cap_disable;
    glad_glEnableClientState=array_enable;glad_glDisableClientState=array_disable;
    glad_glVertexPointer=vertices;glad_glTexCoordPointer=texcoords;glad_glColorPointer=colours;
    glad_glDrawElements=draw;glad_glMatrixMode=no_enum;glad_glPushMatrix=push;glad_glPopMatrix=pop;
    glad_glLoadIdentity=nothing;glad_glScalef=scale;glad_glCullFace=cull_face;
    glad_glShadeModel=no_enum;glad_glColor4f=colour;glad_glTexEnvi=tex_env;glad_glDepthRangef=depth;
    of_alias_mesh_t *m=make_mesh();const void *direct_indices=m->vertices+m->numverts;
    set_alias(0);memset(gl_buf.idx,0xa5,12);memset(gl_buf.tex[0],0x5a,32);
    assert(R_BufferAliasDirect(m));
    for(unsigned i=0;i<12;i++)assert(((unsigned char *)gl_buf.idx)[i]==0xa5);
    for(unsigned i=0;i<32;i++)assert(((unsigned char *)gl_buf.tex[0])[i]==0x5a);
    prepare_data();R_ApplyGLBuffer();
    assert(draws==1&&seen[0].indices==direct_indices&&seen[0].uv==&m->vertices[0].s);
    assert(seen[0].stride==sizeof(m->vertices[0])&&seen[0].textured&&seen[0].count==6);
    assert(!alias_direct&&!idx_ptr&&!vtx_ptr&&!gl_buf.vt&&!gl_buf.tx&&!gl_buf.cl);

    set_alias(0);R_BufferAliasIndices(m);prepare_data();R_ApplyGLBuffer();
    assert(seen[1].indices==gl_buf.idx&&seen[1].uv==gl_buf.tex[0]&&seen[1].stride==0);
    set_alias(0);assert(!R_BufferAliasDirect(NULL));
    gl_buf.type=buf_shadow;assert(!R_BufferAliasDirect(m));gl_buf.type=buf_alias;
    m->numverts=MAX_VERTICES;assert(!R_BufferAliasDirect(m));m->numverts=4;
    m->numindices=MAX_INDICES;assert(!R_BufferAliasDirect(m));m->numindices=6;
    R_SetBufferIndices(GL_TRIANGLE_FAN,3);assert(!R_BufferAliasDirect(m));R_ResetGLBuffer();

    /* Appending ordinary indices first consumes the pending direct view. */
    set_alias(0);assert(R_BufferAliasDirect(m));prepare_data();unsigned before=draws;
    R_BufferAliasIndices(m);assert(draws==before+1&&!alias_direct&&idx_ptr==6);
    prepare_data();R_ApplyGLBuffer();assert(seen[draws-1].indices==gl_buf.idx);
    set_alias(0);assert(R_BufferAliasDirect(m));prepare_data();before=draws;
    R_SetBufferIndices(GL_TRIANGLE_FAN,3);assert(draws==before+1&&!alias_direct&&idx_ptr==3);
    R_ResetGLBuffer();
    set_alias(0);assert(R_BufferAliasDirect(m));prepare_data();before=draws;
    R_Buffer2DQuad(0,0,1,1,0,0,1,1);assert(draws==before+1&&!alias_direct&&idx_ptr==6);
    R_ResetGLBuffer();

    /* Changing to a shadow and to world geometry cannot retain mesh pointers. */
    set_alias(0);assert(R_BufferAliasDirect(m));prepare_data();before=draws;
    R_UpdateGLBuffer(buf_shadow,0,0,0,1);assert(draws==before+1&&!alias_direct);
    R_SetBufferIndices(GL_TRIANGLE_FAN,3);R_ApplyGLBuffer();
    assert(!seen[draws-1].textured&&seen[draws-1].indices==gl_buf.idx);
    set_alias(0);assert(R_BufferAliasDirect(m));prepare_data();before=draws;
    glpoly_t poly={0};poly.of_world=(ofgl_world_poly_t *)(uintptr_t)1;
    assert(R_BufferWorldPoly(&poly));assert(draws==before+1&&!alias_direct&&world_count==1);
    R_ApplyGLBuffer();assert(world_draws==1&&!world_count);

    const int flags[]={RF_TRANSLUCENT,RF_SHELL_RED,RF_SHELL_HALF_DAM,RF_SHELL_DOUBLE,
        RF_TRANSLUCENT|RF_WEAPONMODEL|RF_DEPTHHACK};
    hand.value=1;gunfov.value=-1;
    for(unsigned i=0;i<sizeof(flags)/sizeof(flags[0]);i++) {
        set_alias(flags[i]);assert(R_BufferAliasDirect(m));prepare_data();R_ApplyGLBuffer();
        assert(seen[draws-1].indices==direct_indices);
        assert(seen[draws-1].blend==!!(flags[i]&RF_TRANSLUCENT));
        assert(seen[draws-1].textured==!(flags[i]&(RF_SHELL_RED|RF_SHELL_HALF_DAM|RF_SHELL_DOUBLE)));
        assert(!blend_enabled&&texture_enabled&&!alias_direct);
    }
    assert(pushes==1&&pops==1&&perspectives==1&&cull==GL_FRONT&&depth_low==0&&depth_high==1);
    set_alias(0);assert(R_BufferAliasDirect(m));R_ResetGLBuffer();free(m);
    assert(!alias_direct&&!world_count&&!idx_ptr&&!vtx_ptr);
    gl_buf.type=buf_2d;R_Buffer2DQuad(0,0,1,1,0,0,1,1);R_ApplyGLBuffer();
    assert(seen[draws-1].indices==gl_buf.idx&&seen[draws-1].uv==gl_buf.tex[0]);
    puts("PASS: direct alias GL pointers, UV seams, zero index/UV staging writes, bounded fallback, append/reset lifetime, alpha/shell/weapon/depth/shadow/world transitions");
    return 0;
}
