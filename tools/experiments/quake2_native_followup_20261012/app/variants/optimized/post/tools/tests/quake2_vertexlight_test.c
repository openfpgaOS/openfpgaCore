#define TEST_VERTEX_LIGHTMAP 1
#define main HistoryMain
#include "quake2_lightmap_history_test.c"
#undef main

int main(void)
{
    HistoryMain();
    lightstyle_t styles[256] = {0};
    dlight_t lights[4] = {0};
    r_newrefdef.lightstyles = styles;
    r_newrefdef.dlights = lights;
    r_newrefdef.num_dlights = 4;
    r_framecount = 100;
    mtexinfo_t tex = {0};
    cplane_t plane = {0};
    msurface_t s = {0};
    uint32_t expected[128*128], actual[128*128];
    byte data[4*17*17*3], used[17*17];
    glpoly_t *p = calloc(1, sizeof(glpoly_t) + 64*VERTEXSIZE*sizeof(float));
    assert(p);
    s.polys=p; s.texinfo=&tex; s.plane=&plane;
    s.light_s=31; s.light_t=23;
    tex.vecs[0][0]=tex.vecs[1][1]=1;
    plane.normal[2]=1;
    for (int trial=0;trial<4000;trial++)
    {
        int width=2+Random()%16, height=2+Random()%16;
        int maps=trial%5;
        s.extents[0]=(width-1)*16; s.extents[1]=(height-1)*16;
        s.samples=trial%7 ? data : NULL;
        s.dlightframe=trial%2 ? r_framecount : -1;
        s.dlightbits=Random()&15;
        s.texturemins[0]=(int)(Random()%100)-50;
        s.texturemins[1]=(int)(Random()%100)-50;
        for (int k=0;k<4;k++)
        {
            s.styles[k]=k<maps ? k : 255;
            for (int c=0;c<3;c++) styles[k].rgb[c]=trial%9 ? RandomFloat(-0.1f,3.0f) : 1;
            lights[k].intensity=RandomFloat(0,850);
            for (int c=0;c<3;c++)
            {
                lights[k].origin[c]=RandomFloat(-50,300);
                lights[k].color[c]=RandomFloat(-0.5f,2);
            }
        }
        modulate.value=trial%9 ? RandomFloat(0.2f,2) : 1;
        gl_state.minlight_set=trial%2;
        for (unsigned k=0;k<sizeof(data);k++) data[k]=(byte)Random();
        memset(used,0,sizeof(used));
        p->numverts=1+Random()%32;
        for (int v=0;v<p->numverts;v++)
        {
            int x=Random()%width, y=Random()%height;
            used[y*width+x]=1;
            /* Pixel boundaries as well as interior nearest samples. */
            p->verts[v][5]=(s.light_s+x+(trial%2 ? 0 : 0.5f))/128.0f;
            p->verts[v][6]=(s.light_t+y+(trial%2 ? 0 : 0.5f))/128.0f;
        }
        memset(expected,0x55,sizeof(expected)); memset(actual,0x55,sizeof(actual));
        R_BuildLightMap(&s,(byte *)expected,128*4);
        assert(R_BuildVertexLightMap(&s,(byte *)actual,128*4,NULL));
        for (int y=0;y<128;y++) for(int x=0;x<128;x++)
            assert(actual[y*128+x] ==
                (x<width && y<height && used[y*width+x] ? expected[y*128+x] : 0x55555555u));
        /* Failure must be atomic, leaving the full builder an untouched page. */
        p->verts[p->numverts-1][5]=-1;
        memset(actual,0x55,sizeof(actual));
        assert(!R_BuildVertexLightMap(&s,(byte *)actual,128*4,NULL));
        for (int k=0;k<128*128;k++) assert(actual[k]==0x55555555u);
    }
    s.extents[0]=s.extents[1]=16*16;
    p->numverts=65;
    for (int v=0;v<65;v++)
    {
        p->verts[v][5]=(s.light_s+v%17+0.5f)/128.0f;
        p->verts[v][6]=(s.light_t+v/17+0.5f)/128.0f;
    }
    assert(!R_BuildVertexLightMap(&s,(byte *)actual,128*4,NULL));
    free(p);
    puts("PASS: 4000 exact sparse/full comparisons, 0-4 styles, missing samples, negative lights, gamma/minlight, UV edges and atomic fallback");
    return 0;
}
