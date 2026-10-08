/* Exercise actual mini-GL old indexed and new alias-only paths together. */
#define main retained_renderer_main
#include "renderer_fixture.c"
#undef main
static uint32_t rng=0x4866e535,checksum;
static uint32_t random_word(void) {rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static float random_f(float scale) {return ((int)(random_word()%2049)-1024)*scale;}
static int cases;
static void run_alias_cases(void)
{
    static float source[769][4],expanded[8][7];
    struct {int xyz;float s,t;} map[8];
    unsigned char colors[8][4];
    const GLushort indices[]={0,1,2,3,1,2,0,4,5,6,1,7};
    cpu_triangle_t expected[32];unsigned expected_alpha[32];
    for(int mode=0;mode<5;mode++) {
#if defined(__riscv)
        __asm__ volatile("csrw frm,%0"::"r"(mode):"memory");
#endif
        for(int trial=0;trial<1200;trial++) {
            for(int i=0;i<769;i++) {source[i][0]=random_f(1.0f/32);source[i][1]=random_f(1.0f/32);source[i][2]=-1.0f-(random_word()%1024)*(1.0f/16);source[i][3]=0;}
            int ids[]={0,1,2,0,256,512,768,2};
            for(int i=0;i<8;i++) {
                map[i].xyz=ids[i];map[i].s=random_f(1.0f/256);map[i].t=random_f(1.0f/256);
                memcpy(expanded[i],source[ids[i]],3*sizeof(float));expanded[i][3]=map[i].s;expanded[i][4]=map[i].t;
                for(int j=0;j<4;j++)colors[i][j]=random_word();
            }
            if(trial%8==0) {
                /* Entirely visible shared points, with independent seam UV/color. */
                for(int i=0;i<769;i++)source[i][2]=-64;
                for(int i=0;i<8;i++)expanded[i][2]=-64;
            }
            if(trial%17==0) {
                uint32_t bits=trial&1?0x7fc03210:0x7f800000;
                for(int i=0;i<769;i++)source[i][2]=-64;
                for(int i=0;i<8;i++)expanded[i][2]=-64;
                memcpy(&source[0][0],&bits,4);memcpy(&expanded[0][0],&bits,4);memcpy(&expanded[3][0],&bits,4);
            }
            setup_world_arrays(expanded[0]);gpu_geometry=0;ofgl_geometry_cv->value=0;assert(OFGL_AliasPositionsEnabled());
            ofgl_tex2d_on[1]=0;
            ofgl_TexCoordPointer(2,GL_FLOAT,sizeof(map[0]),&map[0].s);
            ofgl_ColorPointer(4,GL_UNSIGNED_BYTE,0,colors);ofgl_EnableClientState(GL_COLOR_ARRAY);
            ofgl_TexEnvi(GL_TEXTURE_ENV,GL_TEXTURE_ENV_MODE,GL_MODULATE);
            if(trial&1)ofgl_Enable(GL_BLEND);
            if(trial&2)ofgl_Disable(GL_TEXTURE_2D); /* shell-like draw; poison UV pointer */
            if(trial&2)ofgl_TexCoordPointer(2,GL_FLOAT,0,(const void*)1);
            if(trial&4){ofgl_Enable(GL_CULL_FACE);ofgl_CullFace(trial&8?GL_FRONT:GL_BACK);}
            if(trial&8)ofgl_DepthRangef(0,0.3f);
            if(trial&16)ofgl_Scalef(-1,1,1);
            if(trial&32)ofgl_Translatef(random_f(1.0f/128),random_f(1.0f/128),random_f(1.0f/128));
            ofgl_draw_mask=(trial%19)?15:0;
            reset_geometry_recording();OFGL_SetDrawBounds(NULL,NULL);
            ofgl_DrawElements(GL_TRIANGLES,12,GL_UNSIGNED_SHORT,indices);
            int n=draws;for(int i=0;i<n;i++){expected[i]=recorded_cpu_triangle(i);expected_alpha[i]=alpha_seen[i];}
            ofgl_array_t saved=ofgl_va_vertex;
            reset_geometry_recording();OFGL_SetDrawBounds(NULL,NULL);
            OFGL_DrawAliasElements(&map[0].xyz,sizeof(map[0]),source[0],indices,12);
            assert(!memcmp(&saved,&ofgl_va_vertex,sizeof(saved)));assert(draws==n);
            for(int i=0;i<n;i++){check_cpu_triangle(i,&expected[i]);assert(alpha_seen[i]==expected_alpha[i]);checksum=checksum*33+(unsigned)triangle_x[i][0]+alpha_seen[i];}
            /* Cache generation wrap, generic draw following alias, and failed begin. */
            reset_geometry_recording();ofgl_DrawElements(GL_TRIANGLES,12,GL_UNSIGNED_SHORT,indices);assert(draws==n);
            for(int i=0;i<n;i++)check_cpu_triangle(i,&expected[i]);
            if((trial&127)==0) {ofgl_geometry_cv->value=1;assert(!OFGL_AliasPositionsEnabled());ofgl_geometry_cv->value=0;}
            cases++;
        }
    }
#if defined(__riscv)
    __asm__ volatile("csrw frm,zero":::"memory");
#endif
    ofgl_draw_mask=15;
}
int main(void)
{
    assert(RI_InitContext(NULL));run_alias_cases();RI_ShutdownContext();
    assert(RI_InitContext(NULL));RI_ShutdownContext();
    printf("ALIAS_POSITION_TEST cases=%d errors=0 checksum=%08x\n",cases,checksum);
    return 0;
}
