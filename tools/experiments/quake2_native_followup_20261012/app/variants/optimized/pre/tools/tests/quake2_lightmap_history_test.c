/* Compare actual regeneration against the original three-copy updater.
 * All three copy IDs address one RGB565 page in the OpenFPGA backend. */
#include <assert.h>
#include <stdint.h>
#include "../../src/quake2/src/client/refresh/gl1/gl1_surf.c"
#include "../../src/quake2/src/client/refresh/gl1/gl1_light.c"

glconfig_t gl_config;
glstate_t gl_state;
refdef_t r_newrefdef;
int r_framecount, cur_lm_copy;
unsigned char gammatable[256];
static cvar_t modulate, fullbright, dynamic;
cvar_t *r_modulate = &modulate, *r_fullbright = &fullbright, *gl1_dynamic = &dynamic;

#define PAGES 3
#define SURFACES 12
static uint32_t rgba[2][PAGES][BLOCK_WIDTH * BLOCK_HEIGHT];
static uint16_t packed[2][PAGES][BLOCK_WIDTH * BLOCK_HEIGHT];
static msurface_t surfaces[2][SURFACES];
static mtexinfo_t textures[SURFACES];
static cplane_t planes[SURFACES];
static byte samples[SURFACES][4 * 17 * 17 * 3];
static int context, bound_page, bound_texture, copy_index[2];
static uint64_t upload_calls[2], upload_pixels[2];

void TestLightmapUpload(int texnum, int x, int y, int w, int h,
    const void *pixels, uint16_t pages[][BLOCK_WIDTH * BLOCK_HEIGHT]);
void Com_Printf(const char *fmt, ...) { (void)fmt; }

void Com_Error(int code, const char *fmt, ...)
{
    (void)code; (void)fmt; abort();
}
qboolean R_Bind(int texnum)
{
    assert(texnum >= gl_state.lightmap_textures);
    bound_texture = texnum;
    bound_page = (texnum - gl_state.lightmap_textures) % MAX_LIGHTMAPS - 1;
    assert(bound_page >= 0 && bound_page < PAGES);
    return true;
}
static void Upload(GLenum target, GLint level, GLint x, GLint y,
    GLsizei w, GLsizei h, GLenum format, GLenum type, const void *pixels)
{
    assert(target == GL_TEXTURE_2D && level == 0);
    assert(format == GL_LIGHTMAP_FORMAT && type == GL_UNSIGNED_BYTE);
    assert(x >= 0 && y >= 0 && w > 0 && h > 0);
    assert(x+w <= BLOCK_WIDTH && y+h <= BLOCK_HEIGHT);
    upload_calls[context]++;
    upload_pixels[context] += w*h;
    TestLightmapUpload(bound_texture,x,y,w,h,pixels,packed[context]);
}
PFNGLTEXSUBIMAGE2DPROC glad_glTexSubImage2D = Upload;

static void
ReferenceRegenAllLightmaps()
{
	static lmrect_t lmchange[MAX_LIGHTMAPS][MAX_LIGHTMAP_COPIES];
	static qboolean altered[MAX_LIGHTMAPS][MAX_LIGHTMAP_COPIES];

	int i, lmtex;
#ifndef YQ2_GL1_GLES
	qboolean pixelstore_set = false;
#endif

	if ( !gl_config.multitexture || r_fullbright->value || !gl1_dynamic->value )
	{
		return;
	}

	if (gl_config.lightmapcopies)
	{
		cur_lm_copy = (cur_lm_copy + 1) % MAX_LIGHTMAP_COPIES;	// select the next lightmap copy
		lmtex = MAX_LIGHTMAPS * cur_lm_copy;	// ...and its corresponding texture
	}
	else
	{
		lmtex = 0;
	}

	for (i = 1; i < MAX_LIGHTMAPS; i++)
	{
		lmrect_t current, best;
		msurface_t *surf;
		byte *base;
		qboolean affected_lightmap;

		if (!gl_lms.lightmap_surfaces[i] || !gl_lms.lightmap_buffer[i])
		{
			continue;
		}

		affected_lightmap = false;
		best.top = BLOCK_HEIGHT;
		best.left = BLOCK_WIDTH;
		best.bottom = best.right = 0;

		for (surf = gl_lms.lightmap_surfaces[i];
			 surf != 0;
			 surf = surf->lightmapchain)
		{
			int map;

			if (surf->texinfo->flags & (SURF_SKY | SURF_TRANS33 | SURF_TRANS66 | SURF_WARP))
			{
				continue;
			}

			// Any dynamic lights on this surface?
			for (map = 0; map < MAXLIGHTMAPS && surf->styles[map] != 255; map++)
			{
				if (r_newrefdef.lightstyles[surf->styles[map]].white != surf->cached_light[map])
				{
					goto dynamic_surf;
				}
			}

			// Surface is considered to have dynamic lights if it had them in the previous frame
			if ( surf->dlightframe != r_framecount && !surf->dirty_lightmap )
			{
				continue;	// no dynamic lights affect this surface in this frame
			}

dynamic_surf:
			affected_lightmap = true;

			current.left = surf->light_s;
			current.right = surf->light_s + (surf->extents[0] >> 4) + 1;	// + smax
			current.top = surf->light_t;
			current.bottom = surf->light_t + (surf->extents[1] >> 4) + 1;	// + tmax

			base = gl_lms.lightmap_buffer[i];
			base += (current.top * BLOCK_WIDTH + current.left) * LIGHTMAP_BYTES;

			R_BuildLightMap(surf, base, BLOCK_WIDTH * LIGHTMAP_BYTES);

			surf->dirty_lightmap = (surf->dlightframe == r_framecount);
			if (!surf->dirty_lightmap || gl_config.lightmapcopies)
			{
				for (map = 0; map < MAXLIGHTMAPS && surf->styles[map] != 255; map++)
				{
					if ( (surf->styles[map] >= 32) || (surf->styles[map] == 0) )
					{
						R_SetCacheState(surf);
						break;
					}
				}
			}
			R_JoinAreas(&current, &best);
		}

		if (!gl_config.lightmapcopies && !affected_lightmap)
		{
			continue;
		}

		if (gl_config.lightmapcopies)
		{
			// Add all the changes that have happened in the last few frames,
			// at least just for consistency between them.
			qboolean apply_changes = affected_lightmap;
			current = best;		// save state for next frames... +

			for (int k = 0; k < MAX_LIGHTMAP_COPIES; k++)
			{
				if (altered[i][k])
				{
					apply_changes = true;
					R_JoinAreas(&lmchange[i][k], &best);
				}
			}

			altered[i][cur_lm_copy] = affected_lightmap;
			if (affected_lightmap)
			{
				lmchange[i][cur_lm_copy] = current;	// + ...here
			}

			if (!apply_changes)
			{
				continue;
			}
		}

#ifndef YQ2_GL1_GLES
		if (!pixelstore_set)
		{
			glPixelStorei(GL_UNPACK_ROW_LENGTH, BLOCK_WIDTH);
			pixelstore_set = true;
		}
#endif

		// upload changes
		base = gl_lms.lightmap_buffer[i];

#ifdef YQ2_GL1_GLES
		base += (best.top * BLOCK_WIDTH) * LIGHTMAP_BYTES;

		R_Bind(gl_state.lightmap_textures + i + lmtex);

		glTexSubImage2D(GL_TEXTURE_2D, 0, 0, best.top,
			BLOCK_WIDTH, best.bottom - best.top,
			GL_LIGHTMAP_FORMAT, GL_UNSIGNED_BYTE, base);
#else
		base += (best.top * BLOCK_WIDTH + best.left) * LIGHTMAP_BYTES;

		R_Bind(gl_state.lightmap_textures + i + lmtex);

		glTexSubImage2D(GL_TEXTURE_2D, 0, best.left, best.top,
			best.right - best.left, best.bottom - best.top,
			GL_LIGHTMAP_FORMAT, GL_UNSIGNED_BYTE, base);
#endif
	}

#ifndef YQ2_GL1_GLES
	if (pixelstore_set)
	{
		glPixelStorei(GL_UNPACK_ROW_LENGTH, 0);
	}
#endif
}

static uint32_t rng = 0x76543210u;
static uint32_t Random(void)
{
    rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5; return rng;
}
static float RandomFloat(float lo, float hi)
{
    return lo + (hi-lo)*(float)(Random()&65535)/65535.0f;
}
int main(void)
{
    lightstyle_t styles[256] = {0};
    dlight_t lights[4] = {0};
    r_newrefdef.lightstyles = styles;
    r_newrefdef.dlights = lights;
    r_newrefdef.num_dlights = 4;
    gl_state.lightmap_textures = TEXNUM_LIGHTMAPS;
    gl_config.multitexture = true;
    modulate.value = dynamic.value = 1;
    for (int i=0;i<256;i++)
    {
        gammatable[i]=(byte)(255-i);
        minlight[i]=(byte)(64+i*3/4);
    }
    for (int p=0;p<PAGES;p++)
        for (int i=0;i<BLOCK_WIDTH*BLOCK_HEIGHT;i++)
        {
            uint32_t v = Random();
            rgba[0][p][i]=rgba[1][p][i]=v;
            uint16_t rgb = ((v&248u)<<8) | ((v&0xfc00u)>>5) | ((v&0xf80000u)>>19);
            packed[0][p][i]=packed[1][p][i]=rgb;
        }
    for (int i=0;i<SURFACES;i++)
    {
        textures[i].vecs[0][0]=textures[i].vecs[1][1]=1;
        planes[i].normal[2]=1;
        for (unsigned j=0;j<sizeof(samples[i]);j++) samples[i][j]=(byte)Random();
        for (int c=0;c<2;c++)
        {
            msurface_t *s = &surfaces[c][i];
            s->texinfo=&textures[i]; s->plane=&planes[i]; s->samples=samples[i];
            s->lightmaptexturenum=1+i%PAGES;
            s->light_s=(i%PAGES)*40; s->light_t=(i/PAGES)*26;
            s->extents[0]=(1+i%17)*16; s->extents[1]=(1+(i*3)%17)*16;
            s->styles[0]=0; s->styles[1]=i%3 ? 255 : (i%2 ? 1 : 32);
            s->styles[2]=s->styles[3]=255;
        }
    }
    for (int frame=1;frame<=3000;frame++)
    {
        r_framecount=frame;
        gl_config.lightmapcopies = (frame/250)%2;
        gl_state.minlight_set = (frame/31)%2;
        /* Animated styles, separated changed regions, intermittent dynamic
         * lights, invisible surfaces/pages and restored clean lightmaps. */
        for (int k=0;k<256;k++)
        {
            styles[k].white=1+(frame/7+k)%4;
            for (int c=0;c<3;c++) styles[k].rgb[c]=(float)(1+(frame/7+k+c)%5)*0.35f;
        }
        for (int k=0;k<4;k++)
        {
            lights[k].origin[0]=RandomFloat(-64,320);
            lights[k].origin[1]=RandomFloat(-64,320);
            lights[k].origin[2]=RandomFloat(-32,32);
            lights[k].intensity=RandomFloat(0,500);
            for (int c=0;c<3;c++) lights[k].color[c]=RandomFloat(-0.25f,1.5f);
        }
        for (int i=0;i<SURFACES;i++)
        {
            int lit = ((frame+i)%11)<3;
            for (int c=0;c<2;c++)
            {
                surfaces[c][i].dlightframe=lit ? frame : -1;
                surfaces[c][i].dlightbits=1u<<((frame+i)%4);
            }
        }
        for (context=0;context<2;context++)
        {
            cur_lm_copy=copy_index[context];
            memset(gl_lms.lightmap_surfaces,0,sizeof(gl_lms.lightmap_surfaces));
            for (int p=0;p<PAGES;p++) gl_lms.lightmap_buffer[p+1]=(byte *)rgba[context][p];
            for (int i=0;i<SURFACES;i++)
            {
                if ((frame/13+i)%5==0) continue;
                msurface_t *s=&surfaces[context][i];
                s->lightmapchain=gl_lms.lightmap_surfaces[s->lightmaptexturenum];
                gl_lms.lightmap_surfaces[s->lightmaptexturenum]=s;
            }
            if (context) R_RegenAllLightmaps(); else ReferenceRegenAllLightmaps();
            copy_index[context]=cur_lm_copy;
        }
        assert(copy_index[0]==copy_index[1]);
        assert(!memcmp(rgba[0],rgba[1],sizeof(rgba[0])));
        assert(!memcmp(packed[0],packed[1],sizeof(packed[0])));
        for (int i=0;i<SURFACES;i++)
        {
            assert(surfaces[0][i].dirty_lightmap==surfaces[1][i].dirty_lightmap);
            assert(!memcmp(surfaces[0][i].cached_light,surfaces[1][i].cached_light,
                           sizeof(surfaces[0][i].cached_light)));
        }
    }
    assert(upload_calls[1]<upload_calls[0]);
    assert(upload_pixels[1]<upload_pixels[0]);
    printf("PASS: 3000 frames, exact full RGBA/RGB565 pages and style cache; "
           "uploads %llu -> %llu, pixels %llu -> %llu\n",
           (unsigned long long)upload_calls[0],(unsigned long long)upload_calls[1],
           (unsigned long long)upload_pixels[0],(unsigned long long)upload_pixels[1]);
    return 0;
}
