/* Exercise the real mini-GL with a recording GPU and a host framebuffer. */
#include <assert.h>
#include <stdio.h>
#include <stdarg.h>
#ifndef OFGL_SOURCE
#define OFGL_SOURCE "../../src/quake2/openfpga/of_gl1.c"
#endif
#include OFGL_SOURCE

static uint16_t framebuffer[320 * 240];
static uint16_t other_framebuffers[2][320 * 240];
static int draw_buffer, surface_calls;
static ofq2_tri_state_t state;
static int draws, finishes, fast_textures;
static unsigned alpha_seen[32];
static int16_t triangle_x[32][3], triangle_y[32][3];
static int32_t triangle_s[32][3], triangle_t[32][3], triangle_zi[32][3], triangle_depth[32][3];
static uint16_t triangle_rgb[32][3];
static unsigned now_ms;
static cvar_t vars[4];
static struct of_capabilities caps;
static of_video_mode_t reported_mode = {320, 240, 640, OF_VIDEO_MODE_RGB565, 0};
static int display_mode, check_mode_error, set_mode_error, missing_surface;
static int reject_color_mode, color_mode_calls;
static int gpu_init_ok = 1, gpu_rgb = 1;
static int gpu_geometry;
static int pending_menu_quad;
static int opaque_scope, opaque_begin_calls, opaque_end_calls, opaque_scoped_draws;

void R_ApplyGLBuffer(void)
{
	if (!pending_menu_quad) return;
	pending_menu_quad = 0;
	ofgl_Disable(GL_TEXTURE_2D);
	ofgl_Color4f(1, 1, 1, 1);
	ofgl_blit_rect(10, 10, 18, 18, 0, 0, 1, 1);
	ofgl_Enable(GL_TEXTURE_2D);
}

void Com_Printf(const char *fmt, ...) { assert(!opaque_scope); (void)fmt; }
cvar_t *Cvar_Get(const char *name, const char *value, int flags)
{
	int i = !strcmp(name, "ofgl_bisect");
	if (!strcmp(name, "ofgl_world")) i = 2;
	if (!strcmp(name, "ofgl_geometry")) i = 3;
	vars[i].value = strtof(value, NULL);
	(void)flags;
	return &vars[i];
}
unsigned of_time_ms(void) { return now_ms; }
const struct of_capabilities *of_get_caps(void) { return &caps; }
void of_video_init(void) { }
uint8_t *of_video_surface(void)
{
	surface_calls++;
	if (missing_surface) return NULL;
	return (uint8_t *)(draw_buffer ? other_framebuffers[draw_buffer - 1] : framebuffer);
}
void of_video_flip(void)
{
	assert(!pending_menu_quad); /* text must reach the outgoing frame */
	draw_buffer = (draw_buffer + 1) % 3;
}
void of_video_wait_flip(void) { }
void of_video_set_display_mode(int mode) { display_mode = mode; }
void of_video_set_color_mode(int mode)
{
	color_mode_calls++;
	if (!reject_color_mode) reported_mode.color_mode = mode;
}
int of_video_check_mode(const of_video_mode_t *a, of_video_mode_t *b)
{
	assert(a->color_mode == OF_VIDEO_MODE_RGB565);
	*b = *a; b->stride = a->width * 2;
	return check_mode_error;
}
int of_video_set_mode(const of_video_mode_t *a)
{
	(void)a; display_mode = OF_DISPLAY_FRAMEBUFFER;
	return set_mode_error;
}
void of_video_get_mode(of_video_mode_t *out)
{
	*out = reported_mode;
}
int gladLoadGLES1Loader(GLADloadproc proc) { (void)proc; return 1; }
int OFQ2_Init(int stride) { (void)stride; return gpu_init_ok; }
int OFQ2_Supports(uint32_t cap)
{
	if (cap == OFQ2_CAP_GEOMETRY) return gpu_geometry;
	return cap == OFQ2_CAP_VERT_TRI_RGB ? gpu_rgb : 1;
}
int OFQ2_HasFastTexMem(void) { return fast_textures; }
void OFQ2_Finish(void) { assert(!opaque_scope); finishes++; }
void OFQ2_Shutdown(void) { OFQ2_Finish(); }
void OFQ2_BeginOpaqueTriBatch(void) { assert(!opaque_scope); opaque_scope = 1; ++opaque_begin_calls; }
void OFQ2_EndOpaqueTriBatch(void) { assert(opaque_scope); opaque_scope = 0; ++opaque_end_calls; }
void OFQ2_Kick(void) { assert(!opaque_scope); }
void OFQ2_KickNow(void) { assert(!opaque_scope); }
void OFQ2_BindFramebuffer(uint8_t *fb, int stride) { (void)fb; (void)stride; }
void OFQ2_CacheClean(const void *p, uint32_t n) { (void)p; (void)n; }
void OFQ2_TexCreate(ofq2_texture_t *t, const void *p, uint16_t w, uint16_t h, uint32_t n)
{
	memset(t, 0, sizeof(*t)); t->width = w; t->height = h;
	t->flags = fast_textures ? 1 : 0;
	(void)p; (void)n;
}
void OFQ2_TexBindDomain(const ofq2_texture_t *t) { assert(!opaque_scope); (void)t; }
void OFQ2_ClearRect(uint8_t *p, int stride, int w, int h, uint8_t c)
{
	for (int y = 0; y < h; y++) memset(p + y * stride, c, w);
}
int OFQ2_SetTriState(const ofq2_tri_state_t *s) { assert(!opaque_scope); state = *s; return 1; }
static int geometry_draws;
static int geometry_uploads;
static int geometry_clip_draws, geometry_attempts, geometry_fail_at;
static int object_attempts, object_fail_at;
static uint32_t geometry_clip[32][8];
static uint32_t geometry_raw[32][5];
static uint32_t geometry_triangles[256][3][8];
static unsigned geometry_triangle_uploads[256];
static ofq2_object_state_t object_state;
int OFQ2_SetObjectState(const ofq2_object_state_t *s)
{
	if (++object_attempts == object_fail_at) return 0;
	object_state = *s;
	return 1;
}
int OFQ2_DrawGeometryFan(const uint32_t *vertices, unsigned count, unsigned cull)
{
	assert(count >= 3 && count <= 32 && cull <= 2);
	for (unsigned i = 0; i < count; i++) assert(vertices[i * 5] == i);
	memcpy(geometry_raw, vertices, count * 5 * sizeof(uint32_t));
	geometry_draws++;
	return 1;
}
int OFQ2_DrawClipIndexed(const uint32_t *vertices, unsigned count, uint32_t triangle)
{
	assert(count <= 3);
	if (++geometry_attempts == geometry_fail_at) return 0;
	for (unsigned i = 0; i < count; i++)
	{
		assert(vertices[i * 8] < 32);
		memcpy(geometry_clip[vertices[i * 8]], vertices + i * 8, 8 * sizeof(uint32_t));
	}
	geometry_uploads += count;
	assert(geometry_clip_draws < 256);
	for (int i = 0; i < 3; i++)
		memcpy(geometry_triangles[geometry_clip_draws][i],
			geometry_clip[(triangle >> (i * 5)) & 31], 8 * sizeof(uint32_t));
	geometry_triangle_uploads[geometry_clip_draws++] = count;
	geometry_draws++;
	return 1;
}
int OFQ2_DrawVertTriRGB(const int16_t x[3], const int16_t y[3],
	const int32_t s[3], const int32_t t[3], const int32_t zi[3],
	const uint16_t rgb[3], const int32_t depth[3])
{
	assert(draws < 32);
	memcpy(triangle_x[draws], x, sizeof(triangle_x[draws]));
	memcpy(triangle_y[draws], y, sizeof(triangle_y[draws]));
	memcpy(triangle_s[draws], s, sizeof(triangle_s[draws]));
	memcpy(triangle_t[draws], t, sizeof(triangle_t[draws]));
	memcpy(triangle_zi[draws], zi, sizeof(triangle_zi[draws]));
	memcpy(triangle_depth[draws], depth, sizeof(triangle_depth[draws]));
	memcpy(triangle_rgb[draws], rgb, sizeof(triangle_rgb[draws]));
	alpha_seen[draws++] = state.flags & OFQ2_EMIT_BLEND ? state.const_alpha : 255;
	for (int i = 0; i < 3; i++) assert(zi[i] > 0 && depth[i] > 0);
	(void)x; (void)y; (void)s; (void)t; (void)rgb;
	return 1;
}

int OFQ2_DrawVertTriRGBPacked(const uint32_t words[17])
{
	if (opaque_scope) ++opaque_scoped_draws;
	int16_t x[3], y[3]; int32_t s[3], t[3], zi[3], depth[3]; uint16_t rgb[3];
	for (int i = 0; i < 3; i++)
	{
		x[i] = (int16_t)words[i]; y[i] = (int16_t)(words[i] >> 16);
		s[i] = (int32_t)words[3+i]; t[i] = (int32_t)words[6+i];
		zi[i] = (int32_t)words[9+i]; depth[i] = (int32_t)words[14+i];
	}
	rgb[0] = (uint16_t)words[12]; rgb[1] = (uint16_t)(words[12] >> 16); rgb[2] = (uint16_t)words[13];
	return OFQ2_DrawVertTriRGB(x,y,s,t,zi,rgb,depth);
}

static void test_video_modes(void)
{
	int calls = surface_calls;
	check_mode_error = -1;
	assert(!RI_InitContext(NULL) && display_mode == OF_DISPLAY_TERMINAL);
	check_mode_error = 0; set_mode_error = -1;
	assert(!RI_InitContext(NULL) && display_mode == OF_DISPLAY_TERMINAL);
	set_mode_error = 0; reject_color_mode = 1;
	reported_mode.color_mode = OF_VIDEO_MODE_8BIT;
	assert(!RI_InitContext(NULL) && display_mode == OF_DISPLAY_TERMINAL);
	reject_color_mode = 0; reported_mode.color_mode = OF_VIDEO_MODE_RGB565;
	reported_mode.stride = 320;
	assert(!RI_InitContext(NULL) && display_mode == OF_DISPLAY_TERMINAL);
	reported_mode.stride = 641;
	assert(!RI_InitContext(NULL) && display_mode == OF_DISPLAY_TERMINAL);
	reported_mode.stride = 640; reported_mode.width = 640;
	assert(!RI_InitContext(NULL) && display_mode == OF_DISPLAY_TERMINAL);
	reported_mode.width = 320;
	/* A bad mode must be rejected before any framebuffer access. */
	assert(surface_calls == calls);
	missing_surface = 1;
	assert(!RI_InitContext(NULL) && display_mode == OF_DISPLAY_TERMINAL);
	missing_surface = 0;
	/* Compatibility with a runtime that sets resolution first. */
	reported_mode.color_mode = OF_VIDEO_MODE_8BIT;
	calls = color_mode_calls;
	assert(RI_InitContext(NULL) && color_mode_calls == calls + 1);
	assert(ofgl_fb_stride == 640);
	RI_ShutdownContext();
	gpu_init_ok = 0;
	assert(!RI_InitContext(NULL) && display_mode == OF_DISPLAY_TERMINAL);
	gpu_init_ok = 1; gpu_rgb = 0;
	assert(!RI_InitContext(NULL) && display_mode == OF_DISPLAY_TERMINAL);
	assert(!ofgl_zbuf && !ofgl_gpu_ok);
	gpu_rgb = 1;
}

static void test_framebuffers(void)
{
	uint8_t *first = ofgl_fb();
	int calls = surface_calls;
	assert(ofgl_fb() == first && (uint8_t *)ofgl_fb16() == first);
	assert(surface_calls == calls);
	pending_menu_quad = 1;
	RI_EndFrame();
	uint8_t *second = ofgl_fb();
	assert(first != second && surface_calls == calls + 1);
	assert(((uint16_t *)first)[10 * 320 + 10] == 0xffff);
	assert(((uint16_t *)second)[10 * 320 + 10] == 0);
	RI_EndFrame();
	assert(ofgl_fb() != first && ofgl_fb() != second);
	RI_EndFrame();
	assert(ofgl_fb() == first && surface_calls == calls + 3);
}

static void test_particles(void)
{
	float xyz[9][3];
	uint8_t rgba[9][4];
	for (int i = 0; i < 9; i++)
	{
		xyz[i][0] = i % 3 == 1 ? 1 : -1;
		xyz[i][1] = i % 3 == 2 ? 1 : -1;
		xyz[i][2] = -8;
		memset(rgba[i], 255, 3);
		rgba[i][3] = i < 3 ? 0 : i < 6 ? 128 : 224;
	}
	ofgl_MatrixMode(GL_PROJECTION); ofgl_LoadIdentity();
	ofgl_Frustumf(-1, 1, -0.75f, 0.75f, 1, 4096);
	ofgl_MatrixMode(GL_MODELVIEW); ofgl_LoadIdentity();
	ofgl_Viewport(0, 0, 320, 240);
	ofgl_EnableClientState(GL_VERTEX_ARRAY);
	ofgl_EnableClientState(GL_COLOR_ARRAY);
	ofgl_VertexPointer(3, GL_FLOAT, 0, xyz);
	ofgl_ColorPointer(4, GL_UNSIGNED_BYTE, 0, rgba);
	ofgl_Disable(GL_TEXTURE_2D); ofgl_Enable(GL_BLEND);
	draws = 0;
	ofgl_DrawArrays(GL_TRIANGLES, 0, 9);
	assert(draws == 2 && alpha_seen[0] == 128 && alpha_seen[1] == 224);
	draws = 0;
	ofgl_DrawArrays(GL_TRIANGLES, 6, 3);
	assert(draws == 1 && alpha_seen[0] == 224);
	GLushort indices[] = {6, 7, 8, 3, 4, 5};
	draws = 0;
	ofgl_DrawElements(GL_TRIANGLES, 6, GL_UNSIGNED_SHORT, indices);
	assert(draws == 2 && alpha_seen[0] == 224 && alpha_seen[1] == 128);
	ofgl_DisableClientState(GL_VERTEX_ARRAY);
	ofgl_DisableClientState(GL_COLOR_ARRAY);
}

static void test_clip_colour(void)
{
	ofgl_plane_t p = {0, 0, 1, 0};
	ofgl_cvert_t in[3] = {
		{.x = -1, .z = -1, .rgb = 0xf800, .a = 0},
		{.x = 1, .z = 1, .rgb = 0x001f, .a = 255},
		{.y = 1, .z = 1, .rgb = 0x001f, .a = 255}
	}, out[OFGL_CLIP_MAX];
	int n = ofgl_clip_plane(&p, in, 3, out), clipped = 0;
	assert(n == 4);
	for (int i = 0; i < n; i++)
	{
		if (out[i].z == 0)
		{
			assert(out[i].rgb == 0x8010 && out[i].a == 128);
			clipped++;
		}
	}
	assert(clipped == 2);
}

static void test_close_triangles(void)
{
	const float screen[3][2] = {{-100, -400}, {50, 100}, {480, 600}};
	float xyz[3][3];
	ofgl_MatrixMode(GL_PROJECTION); ofgl_LoadIdentity();
	ofgl_Frustumf(-1, 1, -0.75f, 0.75f, 1, 4096);
	ofgl_MatrixMode(GL_MODELVIEW); ofgl_LoadIdentity();
	ofgl_Viewport(0, 0, 320, 240);
	ofgl_EnableClientState(GL_VERTEX_ARRAY);
	ofgl_DisableClientState(GL_COLOR_ARRAY);
	ofgl_Disable(GL_TEXTURE_2D); ofgl_Disable(GL_BLEND); ofgl_Disable(GL_CULL_FACE);
	ofgl_Color4f(1, 1, 1, 1);
	const float first_z[] = {8.0f, 0.5f, -2.0f, 0.0f};
	for (int near = 0; near < 4; near++)
	{
		for (int i = 0; i < 3; i++)
		{
			float z = i == 0 ? first_z[near] : 8.0f;
			xyz[i][0] = (screen[i][0] - 159.5f) * z / 160.0f;
			xyz[i][1] = -(screen[i][1] - 119.5f) * z / 160.0f;
			xyz[i][2] = -z;
		}
		ofgl_VertexPointer(3, GL_FLOAT, 0, xyz);
		draws = 0;
		ofgl_DrawArrays(GL_TRIANGLES, 0, 3);
		assert(draws > 0);
		for (int tri = 0; tri < draws; tri++)
		{
			int lo = triangle_y[tri][0], hi = lo;
			for (int i = 0; i < 3; i++)
			{
				int y = triangle_y[tri][i];
				if (y < lo) lo = y;
				if (y > hi) hi = y;
			}
			/* OS30's subpixel edge divider has a 13-bit denominator. */
			assert(hi - lo < 8192);
			assert(lo >= -128 * 16 && hi <= (240 + 128) * 16);
		}
	}
	ofgl_DisableClientState(GL_VERTEX_ARRAY);
}

static void test_winding_and_nonfinite(void)
{
	float xyz[3][3] = {{-1, -1, -8}, {1, -1, -8}, {-1, 1, -8}};
	GLushort indices[] = {0, 1, 2, 0, 2, 1};
	ofgl_EnableClientState(GL_VERTEX_ARRAY);
	ofgl_VertexPointer(3, GL_FLOAT, 0, xyz);
	ofgl_Enable(GL_CULL_FACE);
	for (int front = 0; front < 2; front++)
	{
		ofgl_FrontFace(front ? GL_CW : GL_CCW);
		for (int face = 0; face < 2; face++)
		{
			ofgl_CullFace(face ? GL_BACK : GL_FRONT);
			draws = 0;
			ofgl_DrawElements(GL_TRIANGLES, 6, GL_UNSIGNED_SHORT, indices);
			assert(draws == 1);
		}
	}
	ofgl_Disable(GL_CULL_FACE);
	draws = 0;
	ofgl_DrawElements(GL_TRIANGLES, 6, GL_UNSIGNED_SHORT, indices);
	assert(draws == 2);
	/* Invalid projections must never reach fixed-point conversion or the GPU. */
	const uint32_t invalid[] = {0x7fc00000u, 0x7f800000u, 0xff800000u};
	for (unsigned i = 0; i < sizeof(invalid) / sizeof(invalid[0]); i++)
	{
		memcpy(&xyz[0][0], &invalid[i], sizeof(float));
		draws = 0;
		ofgl_DrawArrays(GL_TRIANGLES, 0, 3);
		assert(draws == 0);
	}
	ofgl_FrontFace(GL_CCW); ofgl_CullFace(GL_FRONT);
	ofgl_DisableClientState(GL_VERTEX_ARRAY);
}

static void test_texture_lifetime(void)
{
	uint32_t pixels[32 * 32];
	GLuint id = 42;
	memset(pixels, 255, sizeof(pixels));
	ofgl_active_tmu = 0; ofgl_store_texture = 2;
	ofgl_BindTexture(GL_TEXTURE_2D, id);
	ofgl_TexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 32, 32, 0, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
	unsigned bytes = ofgl_3d_tex_bytes;
	assert(bytes > 0);
	int before = finishes;
	ofgl_gpu_pending = 1;
	ofgl_TexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 32, 32, 0, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
	assert(finishes > before && ofgl_3d_tex_bytes == bytes);
	ofgl_DeleteTextures(1, &id);
	assert(ofgl_3d_tex_bytes == 0 && !ofgl_tex[id].gtex_valid);
	fast_textures = 1;
	ofgl_TexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 32, 32, 0, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
	assert(!ofgl_tex[id].texels && ofgl_tex[id].gtex_valid);
	ofgl_DeleteTextures(1, &id);
	assert(!ofgl_tex[id].gtex_valid && ofgl_tex[id].w == 0);
	fast_textures = 0;
}

static void test_rejection_and_cache_collisions(void)
{
	float xyz[513][3] = {{0}};
	uint8_t rgba[513][4] = {{0}};
	const GLushort indices[] = {0, 256, 512};
	xyz[0][0] = -1; xyz[0][1] = -1; xyz[0][2] = -8;
	xyz[256][0] = 1; xyz[256][1] = -1; xyz[256][2] = -8;
	xyz[512][0] = -1; xyz[512][1] = 1; xyz[512][2] = -8;
	memset(rgba[0], 255, 4); memset(rgba[256], 255, 4); memset(rgba[512], 255, 4);
	ofgl_EnableClientState(GL_VERTEX_ARRAY);
	ofgl_EnableClientState(GL_COLOR_ARRAY);
	ofgl_Disable(GL_TEXTURE_2D);
	ofgl_VertexPointer(3, GL_FLOAT, 0, xyz);
	ofgl_ColorPointer(4, GL_UNSIGNED_BYTE, 0, rgba);
	draws = 0;
	ofgl_DrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, indices);
	assert(draws == 1);
	/* No attribute array may be touched for wholly invisible triangles. */
	/* Still inside the guard band, but wholly left of the visible viewport. */
	xyz[0][0] = xyz[512][0] = -10; xyz[256][0] = -9;
	ofgl_ColorPointer(4, GL_UNSIGNED_BYTE, 0, (void *)(uintptr_t)1);
	draws = 0;
	ofgl_DrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, indices);
	assert(draws == 0);
	ofgl_DisableClientState(GL_VERTEX_ARRAY);
	ofgl_DisableClientState(GL_COLOR_ARRAY);
}

static void setup_world_arrays(float *v)
{
	ofgl_MatrixMode(GL_PROJECTION); ofgl_LoadIdentity();
	ofgl_Frustumf(-1, 1, -0.75f, 0.75f, 1, 4096);
	ofgl_MatrixMode(GL_MODELVIEW); ofgl_LoadIdentity();
	ofgl_Viewport(0, 0, 320, 240);
	ofgl_active_tmu = ofgl_client_tmu = 0;
	ofgl_Enable(GL_TEXTURE_2D); ofgl_Disable(GL_BLEND); ofgl_Disable(GL_CULL_FACE);
	ofgl_DisableClientState(GL_COLOR_ARRAY);
	ofgl_EnableClientState(GL_VERTEX_ARRAY);
	ofgl_EnableClientState(GL_TEXTURE_COORD_ARRAY);
	ofgl_VertexPointer(3, GL_FLOAT, 7 * sizeof(float), v);
	ofgl_TexCoordPointer(2, GL_FLOAT, 7 * sizeof(float), v + 3);
	ofgl_BindTexture(GL_TEXTURE_2D, 0);
	ofgl_Color4f(1, 1, 1, 1);
	ofgl_depth_scale = 1;
}

/* Same integer equations as the geometry worker (including Y's subtraction). */
static void project_geometry(int32_t cx, int32_t cy, int32_t cw, int *x, int *y)
{
	int64_t zi = 0x100000000LL / cw;
	int64_t rx = ((int64_t)cx * zi) >> 16;
	int64_t ry = ((int64_t)cy * zi) >> 16;
	*x = object_state.xcenter * 16 + ((object_state.xscale * rx) >> 12);
	*y = object_state.ycenter - ((object_state.yscale * ry) >> 16);
}

static void reset_geometry_recording(void)
{
	draws = geometry_draws = geometry_uploads = geometry_clip_draws = 0;
	geometry_attempts = geometry_fail_at = object_attempts = object_fail_at = 0;
	memset(geometry_clip, 0, sizeof(geometry_clip));
}

static void check_geometry_triangle(int triangle, const float v[][7], const GLushort indices[3])
{
	assert(triangle < geometry_clip_draws);
	for (int i = 0; i < 3; i++)
	{
		const float *source = v[indices[i]];
		const uint32_t *w = geometry_triangles[triangle][i];
		int x, y;
		project_geometry((int32_t)w[1], (int32_t)w[2], (int32_t)w[3], &x, &y);
		assert(abs(x - (int)lrintf((159.5f - 160 * source[0] / source[2]) * 16)) <= 1);
		assert(abs(y - (int)lrintf((119.5f + 160 * source[1] / source[2]) * 16)) <= 1);
		assert((int32_t)w[3] == (int32_t)lrintf(-source[2] * 256));
		assert((int32_t)w[4] == (int32_t)lrintf(source[3] * 8 * 65536));
		assert((int32_t)w[5] == (int32_t)lrintf(source[4] * 8 * 65536));
		assert(w[6] == 0xffff);
		assert((int32_t)w[7] == (int32_t)lrintf(-1073741824.0f / source[2]));
	}
}

typedef struct
{
	int16_t x[3], y[3];
	int32_t s[3], t[3], zi[3], depth[3];
	uint16_t rgb[3];
} cpu_triangle_t;

static cpu_triangle_t recorded_cpu_triangle(int i)
{
	cpu_triangle_t result = {0};
	memcpy(result.x, triangle_x[i], sizeof(result.x));
	memcpy(result.y, triangle_y[i], sizeof(result.y));
	memcpy(result.s, triangle_s[i], sizeof(result.s));
	memcpy(result.t, triangle_t[i], sizeof(result.t));
	memcpy(result.zi, triangle_zi[i], sizeof(result.zi));
	memcpy(result.depth, triangle_depth[i], sizeof(result.depth));
	memcpy(result.rgb, triangle_rgb[i], sizeof(result.rgb));
	return result;
}

static void check_cpu_triangle(int i, const cpu_triangle_t *expected)
{
	cpu_triangle_t actual = recorded_cpu_triangle(i);
	assert(!memcmp(actual.x, expected->x, sizeof(actual.x)));
	assert(!memcmp(actual.y, expected->y, sizeof(actual.y)));
	assert(!memcmp(actual.s, expected->s, sizeof(actual.s)));
	assert(!memcmp(actual.t, expected->t, sizeof(actual.t)));
	assert(!memcmp(actual.zi, expected->zi, sizeof(actual.zi)));
	assert(!memcmp(actual.depth, expected->depth, sizeof(actual.depth)));
	assert(!memcmp(actual.rgb, expected->rgb, sizeof(actual.rgb)));
}

static void test_world_cache(void)
{
	float v[4][7] = {{-4,-4,-32,0,0,0,0}, {4,-4,-32,1,0,0,0},
		{4,4,-32,1,1,0,0}, {-4,4,-32,0,1,0,0}};
	setup_world_arrays(v[0]);
	assert(!OFGL_WorldPolyBytes(2) && !OFGL_WorldPolyBytes(33));
	void *storage = malloc(OFGL_WorldPolyBytes(4));
	ofgl_world_poly_t *p = OFGL_BuildWorldPoly(storage, v[0], 4);
	assert(p && p->raw_ok);
	GLushort indices[] = {0,1,2,0,2,3};
	gpu_geometry = 0;
	draws = 0; ofgl_DrawElements(GL_TRIANGLES, 6, GL_UNSIGNED_SHORT, indices);
	assert(draws == 2);
	int16_t x[2][3], y[2][3];
	memcpy(x, triangle_x, sizeof(x)); memcpy(y, triangle_y, sizeof(y));
	draws = 0; OFGL_DrawWorldPolys(&p, 1);
	assert(draws == 2 && !memcmp(x, triangle_x, sizeof(x)) && !memcmp(y, triangle_y, sizeof(y)));
	gpu_geometry = 1; draws = geometry_draws = 0;
	OFGL_DrawWorldPolys(&p, 1);
	assert(draws == 0 && geometry_draws == 1);
	for (int i = 0; i < 4; i++)
	{
		int32_t cam[3];
		int xyz[] = {(int16_t)geometry_raw[i][1], (int16_t)(geometry_raw[i][1] >> 16),
			(int16_t)geometry_raw[i][2]};
		for (int row = 0; row < 3; row++)
		{
			int64_t sum = 0;
			for (int j = 0; j < 3; j++) sum += (int64_t)object_state.matrix[row][j] * xyz[j];
			cam[row] = (sum >> 16) + object_state.matrix[row][3];
		}
		int px, py; project_geometry(cam[0], cam[1], cam[2], &px, &py);
		int expected_x = lrintf((159.5f + 160 * v[i][0] / 32) * 16);
		int expected_y = lrintf((119.5f - 160 * v[i][1] / 32) * 16);
		/* These exact inputs must agree at shared CPU/GPU edges. A reversed
		 * Y bias shifts 99.5 to 99.5625 pixels and skips a complete scanline. */
		assert(px == expected_x && py == expected_y);
	}
	/* Static colour caching must observe every dynamic lightmap upload. */
	ofgl_tex2d_on[1] = 1; ofgl_tex_bound[1] = OFGL_TEXNUM_LIGHTMAPS + 5;
	ofgl_va_texcoord_tmu[1].enabled = 1;
	ofgl_va_texcoord_tmu[1].ptr = (uint8_t *)&v[0][5];
	uint32_t light = 0xff0000ff;
	ofgl_lm_store(5, 0, 0, 1, 1, &light);
	OFGL_DrawWorldPolys(&p, 1); assert(geometry_raw[0][2] >> 16 == 0xf800);
	light = 0xff00ff00; ofgl_lm_store(5, 0, 0, 1, 1, &light);
	OFGL_DrawWorldPolys(&p, 1); assert(geometry_raw[0][2] >> 16 == 0x07e0);
	ofgl_tex2d_on[1] = 0;
	OFGL_DrawWorldPolys(&p, 1); assert(geometry_raw[0][2] >> 16 == 0xffff);
	/* Fractional positions and polygons crossing the near plane fall back. */
	v[0][0] = -4.1f; p = OFGL_BuildWorldPoly(storage, v[0], 4);
	assert(!p->raw_ok); draws = geometry_draws = 0;
	OFGL_DrawWorldPolys(&p, 1); assert(draws == 2 && geometry_draws == 0);
	v[0][2] = -0.5f; p = OFGL_BuildWorldPoly(storage, v[0], 4);
	draws = geometry_draws = 0; OFGL_DrawWorldPolys(&p, 1);
	assert(draws > 0 && geometry_draws == 0);
	free(storage); gpu_geometry = 0;
}

/* The generic indexed path stays disabled in normal submissions until its
 * shared-edge convention is qualified. Retain direct helper coverage for its
 * cache/fallback machinery without enabling it for actual renderer draws. */
static void draw_test_indexed_elements(GLenum mode, int count, GLenum type, const GLushort *indices)
{
	assert(mode == GL_TRIANGLES && type == GL_UNSIGNED_SHORT);
	const ofq2_texture_t *tex;
	float tw, th;
	if (!ofgl_begin3d(&tex, &tw, &th)) return;
	ofgl_indexed_geometry = gpu_geometry && !ofgl_blend_on;
	for (int i = 0; i + 3 <= count; i += 3)
		ofgl_tri3d(indices[i], indices[i + 1], indices[i + 2], tw, th);
	ofgl_end3d();
}

static void test_world_only_offload(void)
{
	float v[3][7] = {{-4,-4,-32,0,0,0,0}, {4,-4,-32,1,0,0,0}, {0,4,-32,0,1,0,0}};
	GLushort indices[] = {0,1,2};
	setup_world_arrays(v[0]); reset_geometry_recording(); gpu_geometry = 1;
	ofgl_DrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, indices);
	assert(draws == 1 && geometry_draws == 0 && OFGL_AliasPositionsEnabled());
	gpu_geometry = 0;
}

static void test_indexed_geometry(void)
{
	float v[3][7] = {{-4,-4,-32,0,0,0,0}, {4,-4,-32,1,0,0,0}, {0,4,-32,0,1,0,0}};
	GLushort twice[] = {0,1,2,0,1,2};
	setup_world_arrays(v[0]);
	gpu_geometry = 1; draws = geometry_draws = geometry_uploads = 0;
	draw_test_indexed_elements(GL_TRIANGLES, 6, GL_UNSIGNED_SHORT, twice);
	assert(draws == 0 && geometry_draws == 2 && geometry_uploads == 3);
	for (int i = 0; i < 3; i++)
	{
		const uint32_t *w = geometry_clip[i];
		int x, y; project_geometry((int32_t)w[1], (int32_t)w[2], (int32_t)w[3], &x, &y);
		assert(x == (int)lrintf((159.5f + 160 * v[i][0] / 32) * 16));
		assert(y == (int)lrintf((119.5f - 160 * v[i][1] / 32) * 16));
		assert(w[7] == 33554432u);
	}
	/* A new draw must invalidate indices even if all array addresses repeat. */
	v[0][0] = -3; ofgl_depth_scale = 16;
	draw_test_indexed_elements(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, twice);
	assert(geometry_uploads == 6 && geometry_clip[0][7] == 536870912u);
	/* Opaque-only caching preserves varying particle/beam alpha. */
	ofgl_Enable(GL_BLEND); draws = geometry_draws = 0;
	draw_test_indexed_elements(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, twice);
	assert(draws == 1 && geometry_draws == 0);
	ofgl_Disable(GL_BLEND); ofgl_depth_scale = 1;
	/* Unsupported cores retain the complete CPU path. */
	gpu_geometry = 0; draws = 0;
	draw_test_indexed_elements(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, twice);
	assert(draws == 1);
}

static void test_world_object_state_reuse(void)
{
	const float quad[4][7] = {{-4,-4,-32,0,0,0,0}, {4,-4,-32,1,0,0,0},
		{4,4,-32,1,1,0,0}, {-4,4,-32,0,1,0,0}};
	float v[3][4][7];
	void *storage[3];
	ofgl_world_poly_t *polys[3];
	for (int n = 0; n < 3; n++)
	{
		memcpy(v[n], quad, sizeof(quad));
		for (int i = 0; i < 4; i++) v[n][i][0] += n * 2;
		/* The middle polygon is fully visible but cannot use packed raw
		 * positions. Its ordinary 0x4E commands preserve object state. */
		if (n == 1) v[n][0][0] += 0.1f;
		storage[n] = malloc(OFGL_WorldPolyBytes(4));
		assert(storage[n]);
		polys[n] = OFGL_BuildWorldPoly(storage[n], v[n][0], 4);
		assert(polys[n] && polys[n]->raw_ok == (n != 1));
	}
	setup_world_arrays(v[0][0]); reset_geometry_recording(); gpu_geometry = 0;
	OFGL_DrawWorldPolys(polys + 1, 1);
	assert(draws == 2);
	cpu_triangle_t fallback[] = {recorded_cpu_triangle(0), recorded_cpu_triangle(1)};

	ofgl_world_poly_t *eligible[] = {polys[0], polys[2]};
	reset_geometry_recording(); gpu_geometry = 1;
	OFGL_DrawWorldPolys(eligible, 2);
	assert(geometry_draws == 2 && draws == 0 && object_attempts == 1);
	reset_geometry_recording();
	OFGL_DrawWorldPolys(polys, 3);
	assert(geometry_draws == 2 && draws == 2 && object_attempts == 1);
	check_cpu_triangle(0, &fallback[0]); check_cpu_triangle(1, &fallback[1]);

	/* Each subsequent batch rearms once, including identical state, a
	 * changed modelview and a changed projection for the next draw. */
	OFGL_DrawWorldPolys(polys, 3);
	assert(geometry_draws == 4 && draws == 4 && object_attempts == 2);
	ofq2_object_state_t previous = object_state;
	ofgl_Translatef(1, 0, 0);
	OFGL_DrawWorldPolys(polys, 3);
	assert(geometry_draws == 6 && draws == 6 && object_attempts == 3);
	assert(memcmp(&previous, &object_state, sizeof(previous)));
	previous = object_state;
	ofgl_MatrixMode(GL_PROJECTION); ofgl_LoadIdentity();
	ofgl_Frustumf(-0.75f, 0.75f, -0.5f, 0.5f, 1, 4096);
	ofgl_MatrixMode(GL_MODELVIEW);
	OFGL_DrawWorldPolys(polys, 3);
	assert(geometry_draws == 8 && draws == 8 && object_attempts == 4);
	assert(memcmp(&previous, &object_state, sizeof(previous)));

	/* A rejected state upload cannot mark the batch ready: a later eligible
	 * polygon must retry after both intervening CPU fallback polygons. */
	reset_geometry_recording(); object_fail_at = 1;
	OFGL_DrawWorldPolys(polys, 3);
	assert(geometry_draws == 1 && draws == 4 && object_attempts == 2);
	for (int n = 0; n < 3; n++) free(storage[n]);
	reset_geometry_recording(); gpu_geometry = 0;
}

static void test_geometry_slot_collisions(void)
{
	float v[65][7] = {{0}};
	const int source[] = {0, 32, 64};
	for (int k = 0; k < 3; k++)
	{
		v[source[k]][0] = k == 0 ? -4 : k == 1 ? 4 : 0;
		v[source[k]][1] = k == 2 ? 4 : -4;
		v[source[k]][2] = -32;
		v[source[k]][3] = k * 0.25f;
		v[source[k]][4] = k * 0.125f;
	}
	GLushort indices[] = {0,32,64,0,32,64};
	setup_world_arrays(v[0]); reset_geometry_recording(); gpu_geometry = 0;
	draw_test_indexed_elements(GL_TRIANGLES, 6, GL_UNSIGNED_SHORT, indices);
	assert(draws == 2);
	cpu_triangle_t expected[] = {recorded_cpu_triangle(0), recorded_cpu_triangle(1)};
	reset_geometry_recording(); gpu_geometry = 1;
	draw_test_indexed_elements(GL_TRIANGLES, 6, GL_UNSIGNED_SHORT, indices);
	/* The direct GPU cache must fall back before uploading two distinct
	 * vertices to the same hardware slot. */
	assert(draws == 2 && geometry_draws == 0 && geometry_uploads == 0);
	check_cpu_triangle(0, &expected[0]); check_cpu_triangle(1, &expected[1]);
	gpu_geometry = 0;
}

static void test_geometry_eviction(void)
{
	float v[67][7] = {{0}};
	GLushort indices[105];
	for (int i = 0; i < 67; i++)
	{
		v[i][0] = (i % 9) - 4;
		v[i][1] = (i / 9) - 4;
		v[i][2] = -32;
		v[i][3] = i * 0.0625f;
		v[i][4] = i * 0.03125f;
	}
	int count = 0;
	for (int i = 1; i < 67; i += 2)
	{
		indices[count++] = 0;
		indices[count++] = i;
		indices[count++] = i + 1;
	}
	/* Cross two cache turnovers, then revisit vertices that share slots
	 * with the preceding draw. Triangles containing 32 or 64 collide with
	 * anchor 0 within a single primitive and use the CPU fallback. */
	indices[count++] = 0; indices[count++] = 1; indices[count++] = 2;
	indices[count++] = 0; indices[count++] = 65; indices[count++] = 66;
	GLushort colliding[] = {0,31,32, 0,63,64};
	setup_world_arrays(v[0]); reset_geometry_recording(); gpu_geometry = 0;
	draw_test_indexed_elements(GL_TRIANGLES, 6, GL_UNSIGNED_SHORT, colliding);
	assert(draws == 2);
	cpu_triangle_t expected[] = {recorded_cpu_triangle(0), recorded_cpu_triangle(1)};
	reset_geometry_recording(); gpu_geometry = 1;
	draw_test_indexed_elements(GL_TRIANGLES, count, GL_UNSIGNED_SHORT, indices);
	assert(draws == 2 && geometry_draws == 33 && geometry_uploads == 67);
	assert(geometry_triangle_uploads[31] == 2 && geometry_triangle_uploads[32] == 2);
	check_cpu_triangle(0, &expected[0]); check_cpu_triangle(1, &expected[1]);
	for (int i = 0, recorded = 0; i < 35; i++)
		if (i != 15 && i != 31) check_geometry_triangle(recorded++, v, indices + i * 3);
	gpu_geometry = 0;
}

static void test_geometry_partial_fallback(void)
{
	float v[5][7] = {{-4,-4,-32,0,0,0,0}, {4,-4,-32,1,0,0,0},
		{0,4,-32,129,1,0,0}, {0,4,-32,0,1,0,0}, {1,0,-8,0,1,0,0}};
	GLushort indices[] = {0,1,2, 0,1,3, 0,3,4, 0,1,3};
	setup_world_arrays(v[0]); reset_geometry_recording(); gpu_geometry = 0;
	draw_test_indexed_elements(GL_TRIANGLES, 12, GL_UNSIGNED_SHORT, indices);
	assert(draws == 4);
	cpu_triangle_t expected[] = {recorded_cpu_triangle(0), recorded_cpu_triangle(2)};
	reset_geometry_recording(); gpu_geometry = 1;
	draw_test_indexed_elements(GL_TRIANGLES, 12, GL_UNSIGNED_SHORT, indices);
	/* Triangle 0 fails after packing two vertices. Triangle 2 reuses GPU
	 * vertices but contains an ineligible near vertex. Both must retain
	 * exact CPU projection/UV/depth values, and GPU reuse must remain valid. */
	assert(draws == 2 && geometry_draws == 2 && geometry_uploads == 3);
	check_cpu_triangle(0, &expected[0]); check_cpu_triangle(1, &expected[1]);
	check_geometry_triangle(0, v, indices + 3);
	check_geometry_triangle(1, v, indices + 9);
	assert(geometry_triangle_uploads[1] == 0);
	gpu_geometry = 0;
}

static void test_geometry_submission_failure(void)
{
	float v[5][7] = {{-4,-4,-32,0,0,0,0}, {4,-4,-32,1,0,0,0},
		{0,4,-32,0,1,0,0}, {0,-2,-32,0.5f,0,0,0}, {4,4,-32,1,1,0,0}};
	GLushort indices[] = {0,1,2, 0,3,4, 0,1,2, 0,3,4};
	setup_world_arrays(v[0]); reset_geometry_recording(); gpu_geometry = 0;
	draw_test_indexed_elements(GL_TRIANGLES, 12, GL_UNSIGNED_SHORT, indices);
	assert(draws == 4);
	cpu_triangle_t expected[] = {recorded_cpu_triangle(0), recorded_cpu_triangle(1)};
	for (int failure = 0; failure < 3; failure++)
	{
		reset_geometry_recording(); gpu_geometry = 1;
		if (failure < 2) geometry_fail_at = failure + 1;
		else object_fail_at = 1;
		draw_test_indexed_elements(GL_TRIANGLES, 12, GL_UNSIGNED_SHORT, indices);
		/* Failed submission cannot publish slot ownership or spoil resident
		 * vertices. Later triangles retry missing uploads and reuse survivors. */
		int failed = failure == 1 ? 1 : 0;
		assert(draws == 1 && geometry_draws == 3 && geometry_uploads == 5);
		check_cpu_triangle(0, &expected[failed]);
		for (int source = 0, recorded = 0; source < 4; source++)
			if (source != failed) check_geometry_triangle(recorded++, v, indices + source * 3);
	}
	reset_geometry_recording(); gpu_geometry = 0;
}

static void test_projection_matrix_cache(void)
{
	const GLfloat frusta[][6] = {
		{-1.125f, 1.25f, -0.75f, 0.875f, 4, 4096},
		{-0.625f, 0.5f, -0.45f, 0.375f, 2, 2048},
		{-2.25f, 1.75f, -1.5f, 1.625f, 8, 8192}
	};
	const GLfloat orthos[][6] = {
		{0, 320, 240, 0, -1, 1},
		{-12.5f, 227.75f, -71.25f, 182.5f, -16, 8192},
		{0, 0, 240, 0, -1, 1}, /* invalid horizontal extent */
		{0, 320, 240, 240, -1, 1}, /* invalid vertical extent */
		{0, 320, 240, 0, 4, 4} /* invalid depth extent */
	};
	const unsigned order[] = {0,1,0,1,2,0,2,1,2,0};
	const ofgl_mat4 bases[] = {
		{1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1},
		{0.75f,0.125f,0,0, -0.25f,1.5f,0.0625f,0,
			0.5f,0,0.875f,0, 17,-23,41,1}
	};
	/* Alternate world/weapon projections, evict each cache entry, and apply
	 * each hit to different matrices and matrix modes. The cached value must
	 * be the projection matrix, never the previous post-multiplied result. */
	for (unsigned i = 0; i < sizeof(order) / sizeof(*order); i++)
	{
		const GLfloat *a = frusta[order[i]], *base = bases[i & 1];
		ofgl_mat4 expected;
		ofgl_MatrixMode((i & 2) ? GL_MODELVIEW : GL_PROJECTION);
		ofgl_LoadMatrixf(base);
		ofgl_Frustum(a[0], a[1], a[2], a[3], a[4], a[5]);
		memcpy(expected, ofgl_top_matrix(), sizeof(expected));
		ofgl_LoadMatrixf(base); ofgl_obj_dirty = 0;
		ofgl_Frustumf(a[0], a[1], a[2], a[3], a[4], a[5]);
		assert(ofgl_obj_dirty && !memcmp(expected, ofgl_top_matrix(), sizeof(expected)));
	}
	for (unsigned i = 0; i < 20; i++)
	{
		const GLfloat *a = orthos[(i / 2) % 5], *base = bases[i & 1];
		ofgl_mat4 expected;
		ofgl_MatrixMode((i & 2) ? GL_MODELVIEW : GL_PROJECTION);
		ofgl_LoadMatrixf(base); ofgl_obj_dirty = 0;
		ofgl_Ortho(a[0], a[1], a[2], a[3], a[4], a[5]);
		int expected_dirty = ofgl_obj_dirty;
		memcpy(expected, ofgl_top_matrix(), sizeof(expected));
		ofgl_LoadMatrixf(base); ofgl_obj_dirty = 0;
		ofgl_Orthof(a[0], a[1], a[2], a[3], a[4], a[5]);
		assert(ofgl_obj_dirty == expected_dirty);
		assert(!memcmp(expected, ofgl_top_matrix(), sizeof(expected)));
	}
	ofgl_MatrixMode(GL_MODELVIEW);
}

static void check_bounded_draw(float v[3][7], const float mins[3], const float maxs[3], int inside)
{
	const GLushort indices[] = {0,1,2};
	for (int cap = 0; cap < 2; cap++)
	{
		cpu_triangle_t expected[32];
		uint32_t expected_gpu[3][8];
		gpu_geometry = cap; reset_geometry_recording();
		OFGL_SetDrawBounds(NULL, NULL);
		ofgl_DrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, indices);
		int cpu_count = draws, gpu_count = geometry_clip_draws, uploads = geometry_uploads;
		assert(gpu_count <= 1);
		for (int i = 0; i < cpu_count; i++) expected[i] = recorded_cpu_triangle(i);
		if (gpu_count) memcpy(expected_gpu, geometry_triangles[0], sizeof(expected_gpu));
		reset_geometry_recording(); OFGL_SetDrawBounds(mins, maxs);
		ofgl_DrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, indices);
		assert(!ofgl_draw_bounds_valid && ofgl_batch_inside == inside);
		assert(draws == cpu_count && geometry_clip_draws == gpu_count && geometry_uploads == uploads);
		for (int i = 0; i < cpu_count; i++) check_cpu_triangle(i, &expected[i]);
		if (gpu_count) assert(!memcmp(expected_gpu, geometry_triangles[0], sizeof(expected_gpu)));
	}
	(void)v;
	gpu_geometry = 0;
}

static void test_draw_bounds(void)
{
	float v[3][7] = {{-4,-4,-32,0,0,0,0}, {4,-4,-32,1,0,0,0}, {0,4,-32,0,1,0,0}};
	const float mins[] = {-4,-4,-32}, maxs[] = {4,4,-32};
	const GLushort indices[] = {0,1,2};
	setup_world_arrays(v[0]);
	check_bounded_draw(v, mins, maxs, 1);
	/* A completed bounded draw cannot disable clipping on the next draw. */
	v[2][2] = -0.5f;
	reset_geometry_recording();
	ofgl_DrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, indices);
	assert(!ofgl_batch_inside && !ofgl_draw_bounds_valid);
	assert(ofgl_vmemo[2].clipcode & 1);
	float near_maxs[] = {4,4,-0.5f};
	check_bounded_draw(v, mins, near_maxs, 0);
	assert(ofgl_vmemo[2].clipcode & 1);
	v[2][2] = -32; v[0][0] = -64;
	float crossing_mins[] = {-64,-4,-32};
	check_bounded_draw(v, crossing_mins, maxs, 0);
	assert(ofgl_vmemo[0].viewcode & 1);
	v[0][0] = -4;

	/* Invalid input cancels a previously valid hint. Bit-pattern NaNs also
	 * exercise the finite check used by the fast-math target build. */
	for (int invalid = 0; invalid < 6; invalid++)
	{
		float lo[3], hi[3];
		memcpy(lo, mins, sizeof(lo)); memcpy(hi, maxs, sizeof(hi));
		if (invalid < 3)
		{
			uint32_t bits = invalid == 0 ? 0x7fc00000u : invalid == 1 ? 0x7f800000u : 0xff800000u;
			memcpy(&hi[1], &bits, sizeof(bits));
		}
		else if (invalid == 3) lo[0] = hi[0] + 1;
		else if (invalid == 4) lo[0] = hi[0] = 3.0e38f; /* overflowing centre */
		OFGL_SetDrawBounds(mins, maxs); assert(ofgl_draw_bounds_valid);
		OFGL_SetDrawBounds(invalid == 5 ? NULL : lo, hi);
		assert(!ofgl_draw_bounds_valid && !ofgl_batch_inside);
		reset_geometry_recording();
		ofgl_DrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, indices);
		assert(!ofgl_batch_inside && draws == 1);
	}

	/* begin3d consumes the hint even when the current projection cannot
	 * render 3D, so restoring perspective cannot resurrect stale bounds. */
	OFGL_SetDrawBounds(mins, maxs);
	ofgl_MatrixMode(GL_PROJECTION); ofgl_LoadIdentity();
	reset_geometry_recording();
	ofgl_DrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, indices);
	assert(!ofgl_draw_bounds_valid && !ofgl_batch_inside && draws == 0);
	setup_world_arrays(v[0]); v[2][2] = -0.5f;
	ofgl_DrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, indices);
	assert(!ofgl_batch_inside && (ofgl_vmemo[2].clipcode & 1));
	v[2][2] = -32;

	/* Bounds are in model space: containment uses the transform and weapon
	 * projection installed at submission, including changes after the hint. */
	setup_world_arrays(v[0]); check_bounded_draw(v, mins, maxs, 1);
	OFGL_SetDrawBounds(mins, maxs);
	ofgl_Translatef(30, 0, 0);
	reset_geometry_recording();
	ofgl_DrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, indices);
	assert(!ofgl_batch_inside && (ofgl_vmemo[1].viewcode & 2));
	check_bounded_draw(v, mins, maxs, 0);
	setup_world_arrays(v[0]); check_bounded_draw(v, mins, maxs, 1);
	OFGL_SetDrawBounds(mins, maxs);
	ofgl_MatrixMode(GL_PROJECTION); ofgl_LoadIdentity();
	ofgl_Frustumf(-0.01f, 0.01f, -0.0075f, 0.0075f, 1, 4096);
	ofgl_MatrixMode(GL_MODELVIEW); ofgl_DepthRangef(0, 0.3f);
	reset_geometry_recording();
	ofgl_DrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, indices);
	assert(!ofgl_batch_inside && ofgl_vmemo[0].viewcode);
	check_bounded_draw(v, mins, maxs, 0);

	/* The one-pixel containment margin changes with the viewport scale.
	 * A box inside that margin at width 4 is outside it at width 320. */
	setup_world_arrays(v[0]);
	for (int i = 0; i < 3; i++) v[i][0] += 44;
	float edge_mins[] = {40,-4,-32}, edge_maxs[] = {48,4,-32};
	ofgl_Viewport(17, 23, 4, 120);
	check_bounded_draw(v, edge_mins, edge_maxs, 1);
	OFGL_SetDrawBounds(edge_mins, edge_maxs);
	ofgl_Viewport(0, 0, 320, 240);
	reset_geometry_recording();
	ofgl_DrawElements(GL_TRIANGLES, 3, GL_UNSIGNED_SHORT, indices);
	assert(!ofgl_batch_inside && (ofgl_vmemo[0].viewcode & 2));
	assert(draws == 0 && geometry_draws == 0);
	OFGL_SetDrawBounds(NULL, NULL);
}

static void test_hud_compositing(void)
{
	uint32_t pixels[] = {0xff0000ff, 0, 0xff00ff00, 0xffff0000};
	GLuint id = 43;
	for (int i = 0; i < 320 * 240; i++) framebuffer[i] = 0xffff;
	ofgl_active_tmu = ofgl_client_tmu = 0;
	ofgl_store_texture = 1;
	ofgl_Enable(GL_TEXTURE_2D);
	ofgl_EnableClientState(GL_TEXTURE_COORD_ARRAY);
	ofgl_BindTexture(GL_TEXTURE_2D, id);
	ofgl_TexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 2, 2, 0, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
	ofgl_Color4f(1, 1, 1, 1);
	int before = finishes;
	ofgl_gpu_pending = 1;
	ofgl_blit_rect(10, 10, 12, 12, 0, 0, 1, 1);
	assert(finishes == before + 1);
	assert(framebuffer[10 * 320 + 10] == 0xf800);
	assert(framebuffer[10 * 320 + 11] == 0xffff); /* transparent texel */
	assert(framebuffer[11 * 320 + 10] == 0x07e0);
	assert(framebuffer[11 * 320 + 11] == 0x001f);
	assert(framebuffer[10 * 320 + 9] == 0xffff);
	ofgl_Disable(GL_TEXTURE_2D);
	ofgl_Color4f(0, 0, 0, 0.5f);
	ofgl_blit_rect(0, 0, 1, 1, 0, 0, 0, 0);
	assert(framebuffer[0] == 0x7bef);
	ofgl_DeleteTextures(1, &id);
	ofgl_DisableClientState(GL_TEXTURE_COORD_ARRAY);
}


static void test_opaque_alias_scope(void)
{
	float positions[3][4] = {{-1,-1,-8,0}, {1,-1,-8,0}, {-1,1,-8,0}};
	int sources[3] = {0,1,2};
	GLushort indices[3] = {0,1,2};
	ofgl_MatrixMode(GL_PROJECTION); ofgl_LoadIdentity();
	ofgl_Frustumf(-1, 1, -0.75f, 0.75f, 1, 4096);
	ofgl_MatrixMode(GL_MODELVIEW); ofgl_LoadIdentity();
	ofgl_Viewport(0,0,320,240);
	ofgl_EnableClientState(GL_VERTEX_ARRAY);
	ofgl_DisableClientState(GL_COLOR_ARRAY);
	ofgl_DisableClientState(GL_TEXTURE_COORD_ARRAY);
	ofgl_Disable(GL_TEXTURE_2D); ofgl_Disable(GL_BLEND); ofgl_Disable(GL_CULL_FACE);
	ofgl_Color4f(1,1,1,1);
	ofgl_bisect_cv->value = 0;
	int begin = opaque_begin_calls, end = opaque_end_calls, scoped = opaque_scoped_draws;
	draws = 0;
	OFGL_DrawAliasElements(sources, sizeof(int), &positions[0][0], indices, 3);
	assert(draws == 1 && opaque_begin_calls == begin+1 && opaque_end_calls == end+1);
	assert(opaque_scoped_draws == scoped+1 && !opaque_scope);
	int16_t saved_x[3], saved_y[3]; int32_t saved_z[3];
	memcpy(saved_x, triangle_x[0], sizeof saved_x); memcpy(saved_y, triangle_y[0], sizeof saved_y);
	memcpy(saved_z, triangle_depth[0], sizeof saved_z);
	// Diagnostic exclusion is an independent path through the same emitter.
	ofgl_bisect_cv->value = 1; ofgl_dbg_big = 20;
	draws = 0;
	OFGL_DrawAliasElements(sources, sizeof(int), &positions[0][0], indices, 3);
	assert(draws == 1 && opaque_begin_calls == begin+1 && opaque_scoped_draws == scoped+1);
	assert(!memcmp(saved_x,triangle_x[0],sizeof saved_x));
	assert(!memcmp(saved_y,triangle_y[0],sizeof saved_y));
	assert(!memcmp(saved_z,triangle_depth[0],sizeof saved_z));
	ofgl_bisect_cv->value = 0;
	ofgl_Enable(GL_BLEND); ofgl_Color4f(1,1,1,0.5f); draws = 0;
	OFGL_DrawAliasElements(sources, sizeof(int), &positions[0][0], indices, 3);
	assert(draws == 1 && opaque_begin_calls == begin+1 && alpha_seen[0] == 128);
	ofgl_Disable(GL_BLEND); ofgl_Color4f(1,1,1,1);
	// A near-plane crossing expands one source triangle into two; both stay
	// under the one opaque state and scope, with identical clipping math.
	positions[0][2] = -0.5f; draws = 0; scoped = opaque_scoped_draws;
	OFGL_DrawAliasElements(sources, sizeof(int), &positions[0][0], indices, 3);
	assert(draws >= 2 && opaque_scoped_draws == scoped+draws && !opaque_scope);
	assert(opaque_begin_calls == begin+2 && opaque_end_calls == end+2);
	// Completely culled and rejected calls must not leave a held cursor.
	for (int i=0;i<3;i++) positions[i][2] = 8;
	draws = 0;
	OFGL_DrawAliasElements(sources, sizeof(int), &positions[0][0], indices, 3);
	assert(!draws && !opaque_scope && opaque_begin_calls == begin+3 && opaque_end_calls == end+3);
	OFGL_DrawAliasElements(NULL, sizeof(int), &positions[0][0], indices, 3);
	assert(!opaque_scope && opaque_begin_calls == begin+3);
	ofgl_DisableClientState(GL_VERTEX_ARRAY);
}

int main(int argc, char **argv)
{
	test_video_modes();
	assert(RI_InitContext(NULL));
	if (argc == 2 && !strcmp(argv[1], "particles")) test_particles();
	else if (argc == 2 && !strcmp(argv[1], "clip")) test_clip_colour();
	else if (argc == 2 && !strcmp(argv[1], "textures")) test_texture_lifetime();
	else
	{
		assert(ofgl_bisect_cv->value == 0);
		assert(!RI_GetProcAddress("glPointParameterf"));
		assert(!RI_GetProcAddress("glPointParameterfvARB"));
		assert(!RI_GetProcAddress("glPointParameterfEXT"));
		for (now_ms = 0; now_ms < 40000; now_ms += 1000)
		{
			ofgl_bisect_tick(); assert(ofgl_draw_mask == 15);
		}
		test_framebuffers();
		test_particles(); test_clip_colour(); test_texture_lifetime();
		test_close_triangles();
		test_winding_and_nonfinite();
		test_rejection_and_cache_collisions();
		test_world_cache(); test_world_only_offload(); test_indexed_geometry();
		test_world_object_state_reuse();
		test_geometry_slot_collisions(); test_geometry_eviction();
		test_geometry_partial_fallback(); test_geometry_submission_failure();
		test_projection_matrix_cache();
		test_draw_bounds();
		test_hud_compositing();
		test_opaque_alias_scope();
	}
	ofgl_lm_page(0);
	RI_ShutdownContext();
	assert(!ofgl_zbuf && !ofgl_lm_bytes && !ofgl_flat_ready);
	assert(RI_InitContext(NULL));
	RI_ShutdownContext();
	puts("Quake2 renderer regressions passed");
	return 0;
}
