/* Compare cached MD2 submission with the original strip/fan path, and
 * conservative model culling with explicit transformed box corners. */
#include <assert.h>
#include <stddef.h>
#include "../../src/quake2/src/client/refresh/gl1/gl1_mesh.c"
#include "../../src/quake2/src/client/refresh/gl1/gl1_aliascache.c"

glbuffer_t gl_buf;
glstate_t gl_state;
cplane_t frustum[4];
unsigned char gammatable[256], minlight[256];
static cvar_t showbbox;
cvar_t *gl_showbbox = &showbbox;
static cvar_t shadows;
cvar_t *gl_shadows = &shadows;
int OFGL_NativeAvailable(void) { return 0; }
int R_DrawNativeAlias(const ofgl_native_draw_t *draw) { (void)draw; assert(0); return 0; }
static int vertices, indices;
static int direct_enabled, source_positions_enabled;
static const float *direct_positions;
static const of_alias_mesh_t *direct_mesh;
static int bounds_enabled, bounds_calls;
static vec3_t draw_mins, draw_maxs;
void Com_DPrintf(const char *fmt, ...) { (void)fmt; }

void OFGL_SetDrawBounds(const float mins[3], const float maxs[3])
{
	assert((mins == NULL) == (maxs == NULL));
	bounds_enabled = mins != NULL;
	if (bounds_enabled)
	{
		VectorCopy(mins, draw_mins); VectorCopy(maxs, draw_maxs);
		bounds_calls++;
	}
}

void R_SetBufferIndices(GLenum primitive, GLuint count)
{
	assert(!bounds_enabled); /* Buffer construction may flush preceding draws. */
	assert(primitive == GL_TRIANGLE_STRIP || primitive == GL_TRIANGLE_FAN);
	for (int i = 2; i < count; i++)
	{
		gl_buf.idx[indices++] = vertices + (primitive == GL_TRIANGLE_FAN ? 0 : (i & 1) ? i : i - 2);
		gl_buf.idx[indices++] = vertices + i - 1;
		gl_buf.idx[indices++] = vertices + (primitive == GL_TRIANGLE_FAN ? i : (i & 1) ? i - 2 : i);
	}
	vertices += count;
}
int R_BufferAliasDirect(const of_alias_mesh_t *mesh)
{
	if (!direct_enabled) return 0;
	assert(!bounds_enabled && !vertices && !indices);
	direct_mesh = mesh; vertices = mesh->numverts; indices = mesh->numindices;
	return 1;
}
int R_BufferAliasPositions(const float *positions)
{
	if (!source_positions_enabled) return 0;
	assert(direct_mesh && positions);
	direct_positions = positions;
	return 1;
}
void R_BufferAliasIndices(const of_alias_mesh_t *mesh)
{
	assert(!bounds_enabled);
	const unsigned short *source = (const unsigned short *)(mesh->vertices + mesh->numverts);
	for (int i = 0; i < mesh->numindices; i++) gl_buf.idx[indices++] = vertices + source[i];
	vertices += mesh->numverts;
}

static uint32_t random_state = 0x12540bac;
static float random_float(float lo, float hi)
{
	random_state = random_state * 1664525u + 1013904223u;
	return lo + (hi - lo) * (random_state >> 8) / 16777216.0f;
}
static void command_vertex(int *dst, int xyz, float s, float t)
{
	memcpy(dst, &s, 4); memcpy(dst + 1, &t, 4); dst[2] = xyz;
}
static dmdl_t *new_model(void)
{
	dmdl_t *h = calloc(1, 1024);
	assert(h);
	h->num_xyz = 4; h->num_frames = 2; h->num_glcmds = 24;
	h->framesize = offsetof(daliasframe_t, verts) + 4 * sizeof(dtrivertx_t);
	h->ofs_frames = sizeof(*h);
	h->ofs_glcmds = h->ofs_frames + 2 * h->framesize;
	int *c = (int *)((byte *)h + h->ofs_glcmds);
	c[0] = 4;
	for (int i = 0; i < 4; i++) command_vertex(c + 1 + 3 * i, i, i & 1, i >> 1);
	c[13] = -3;
	command_vertex(c + 14, 0, .125f, 0); /* A texture seam. */
	command_vertex(c + 17, 2, 0, 1);
	command_vertex(c + 20, 3, 1, 1);
	return h;
}
static daliasframe_t *frame(dmdl_t *h, int number)
{
	return (daliasframe_t *)((byte *)h + h->ofs_frames + number * h->framesize);
}
static void reset_buffer(void)
{
	gl_buf.vt = gl_buf.tx = gl_buf.cl = vertices = indices = 0;
	direct_mesh = NULL;
	direct_positions = NULL;
	OFGL_SetDrawBounds(NULL, NULL); bounds_calls = 0;
}
static void check_buffer_bounds(void)
{
	assert(bounds_enabled && bounds_calls == 1);
	for (unsigned i = 0; i < gl_buf.vt; i++)
	{
		float value = direct_positions ? direct_positions[4 * direct_mesh->vertices[i / 3].xyz + i % 3] : gl_buf.vtx[i];
		assert(value >= draw_mins[i % 3] && value <= draw_maxs[i % 3]);
	}
}
static void check_submission(dmdl_t *h)
{
	of_alias_mesh_t *mesh = R_BuildAliasMesh(h);
	assert(mesh && mesh->numverts == 5);
	model_t model = {.extradata = h};
	entity_t e = {.frame = 1, .oldframe = 0};
	float positions[27], texcoords[18];
	byte colours[36];
	const int flags[] = {0, RF_TRANSLUCENT, RF_SHELL_RED, RF_SHELL_BLUE,
		RF_SHELL_HALF_DAM, RF_SHELL_DOUBLE, RF_SHELL_GREEN | RF_TRANSLUCENT};
	/* Also wrap the 16-bit per-normal colour generation counter. */
	for (int iteration = 0; iteration < 65540; iteration++)
	{
		e.flags = flags[iteration % (sizeof(flags) / sizeof(flags[0]))];
		e.alpha = random_float(0, 1);
		for (int i = 0; i < 3; i++)
		{
			e.origin[i] = random_float(-200, 200);
			e.oldorigin[i] = iteration & 1 ? e.origin[i] : random_float(-200, 200);
			e.angles[i] = random_float(-360, 360);
			shadelight[i] = random_float(-.5f, 2);
			for (int f = 0; f < 2; f++)
			{
				frame(h, f)->scale[i] = random_float(.01f, 2);
				frame(h, f)->translate[i] = random_float(-100, 100);
				for (int v = 0; v < 4; v++)
				{
					frame(h, f)->verts[v].v[i] = (byte)random_float(0, 256);
					frame(h, f)->verts[v].lightnormalindex = (byte)random_float(0, NUMVERTEXNORMALS);
				}
			}
		}
		gl_state.minlight_set = iteration & 1;
		shadedots = r_avertexnormal_dots[iteration & 15];
		float backlerp = iteration % 3 ? random_float(0, 1) : 0;
		reset_buffer(); model.of_alias = NULL;
		R_DrawAliasFrameLerp(&e, &model, h, backlerp);
		check_buffer_bounds();
		assert(indices == 9);
		int textured = gl_buf.tx != 0;
		for (int k = 0; k < 9; k++)
		{
			int index = gl_buf.idx[k];
			memcpy(positions + 3 * k, gl_buf.vtx + 3 * index, 3 * sizeof(float));
			if (textured) memcpy(texcoords + 2 * k, gl_buf.tex[0] + 2 * index, 2 * sizeof(float));
			memcpy(colours + 4 * k, gl_buf.clr + 4 * index, 4);
		}
		reset_buffer(); model.of_alias = mesh;
		direct_enabled = iteration & 1;
		source_positions_enabled = iteration & 2;
		R_DrawAliasFrameLerp(&e, &model, h, backlerp);
		check_buffer_bounds();
		assert(indices == 9 && vertices == 5);
		assert(gl_buf.vt == 15 && gl_buf.cl == 20 && gl_buf.tx == (textured && !direct_enabled ? 10 : 0));
		for (int k = 0; k < 9; k++)
		{
			const unsigned short *draw_indices = direct_mesh ?
				(const unsigned short *)(direct_mesh->vertices + direct_mesh->numverts) : gl_buf.idx;
			int index = draw_indices[k];
			const float *position = direct_positions ? direct_positions + 4 * direct_mesh->vertices[index].xyz : gl_buf.vtx + 3 * index;
			assert(!memcmp(positions + 3 * k, position, 3 * sizeof(float)));
			const float *uv = direct_mesh ? &direct_mesh->vertices[index].s : gl_buf.tex[0] + 2 * index;
			if (textured) assert(!memcmp(texcoords + 2 * k, uv, 2 * sizeof(float)));
			assert(!memcmp(colours + 4 * k, gl_buf.clr + 4 * index, 4));
		}
	}
	R_FreeAliasMesh(mesh);
}

static void check_interpolation_bounds(void)
{
	entity_t entity = {0};
	const int flags[5] = {RF_SHELL_RED, RF_SHELL_GREEN, RF_SHELL_BLUE,
		RF_SHELL_DOUBLE, RF_SHELL_HALF_DAM};
	dtrivertx_t old[16], current[16];
	vec4_t positions[16];
	for (int iteration = 0; iteration < 32000; iteration++)
	{
		vec3_t move, front, back;
		entity.flags = 0;
		for (int f = 0; f < 5; f++)
			if (iteration & (1 << f)) entity.flags |= flags[f];
		float backlerp = iteration % 3 ? random_float(-4, 4) : (float)(iteration & 1);
		for (int axis = 0; axis < 3; axis++)
		{
			move[axis] = random_float(-8192, 8192);
			front[axis] = (1 - backlerp) * random_float(-16, 16);
			back[axis] = backlerp * random_float(-16, 16);
			for (int v = 0; v < 16; v++)
			{
				/* All four pairs of interpolation endpoints on each axis. */
				current[v].v[axis] = v & (1 << axis) ? 255 : 0;
				old[v].v[axis] = v & (1 << (axis + 1)) ? 255 : 0;
				current[v].lightnormalindex = (iteration + v) % NUMVERTEXNORMALS;
			}
		}
		R_LerpVerts(&entity, 16, current, old, current, positions[0], move, front, back);
		R_SetAliasDrawBounds(&entity, move, front, back);
		assert(bounds_enabled);
		for (int v = 0; v < 16; v++)
			for (int axis = 0; axis < 3; axis++)
				assert(positions[v][axis] >= draw_mins[axis] && positions[v][axis] <= draw_maxs[axis]);
	}
	/* Reject nonfinite bounds even under the production fast-math flags. */
	const uint32_t invalid[] = {0x7f800000u, 0xff800000u, 0x7fc00000u};
	for (int k = 0; k < 3; k++)
	{
		vec3_t move = {0}, front = {1, 1, 1}, back = {0};
		memcpy(&move[1], &invalid[k], sizeof(float));
		R_SetAliasDrawBounds(&entity, move, front, back);
		assert(!bounds_enabled);
	}
}
static void reference_corners(dmdl_t *h, entity_t *e, vec3_t corners[8])
{
	vec3_t mins, maxs, vectors[3], angles;
	daliasframe_t *current = frame(h, e->frame), *old = frame(h, e->oldframe);
	for (int axis = 0; axis < 3; axis++)
	{
		mins[axis] = fminf(current->translate[axis], old->translate[axis]);
		maxs[axis] = fmaxf(current->translate[axis] + current->scale[axis] * 255,
			old->translate[axis] + old->scale[axis] * 255);
	}
	VectorCopy(e->angles, angles); angles[YAW] = -angles[YAW];
	AngleVectors(angles, vectors[0], vectors[1], vectors[2]);
	for (int i = 0; i < 8; i++)
	{
		vec3_t point;
		for (int axis = 0; axis < 3; axis++) point[axis] = i & (1 << axis) ? mins[axis] : maxs[axis];
		corners[i][0] = DotProduct(vectors[0], point) + e->origin[0];
		corners[i][1] = -DotProduct(vectors[1], point) + e->origin[1];
		corners[i][2] = DotProduct(vectors[2], point) + e->origin[2];
	}
}
static void check_culling(dmdl_t *h)
{
	model_t model = {.extradata = h};
	entity_t e = {.frame = 1, .oldframe = 0};
	for (int iteration = 0; iteration < 100000; iteration++)
	{
		e.frame = iteration & 1;
		e.oldframe = (iteration >> 1) & 1;
		for (int axis = 0; axis < 3; axis++)
		{
			e.origin[axis] = random_float(-8192, 8192);
			e.angles[axis] = random_float(-360, 360);
			for (int f = 0; f < 2; f++)
			{
				frame(h, f)->scale[axis] = iteration % 7 ? random_float(-2, 2) : 0;
				frame(h, f)->translate[axis] = random_float(-256, 256);
			}
		}
		if (iteration % 3 == 0) e.angles[PITCH] = e.angles[ROLL] = 0;
		if (iteration % 9 == 0) e.angles[YAW] = 0;
		vec3_t corners[8], actual[8]; reference_corners(h, &e, corners);
		int reference_culled = 0;
		float greatest_gap = 0;
		for (int f = 0; f < 4; f++)
		{
			for (int axis = 0; axis < 3; axis++) frustum[f].normal[axis] = random_float(-1, 1);
			VectorNormalize(frustum[f].normal);
			float support = -FLT_MAX;
			for (int p = 0; p < 8; p++) support = fmaxf(support, DotProduct(frustum[f].normal, corners[p]));
			frustum[f].dist = support + random_float(iteration & 1 ? -.001f : -10, iteration & 1 ? .001f : 10);
			float gap = frustum[f].dist - support;
			if (gap > 0) reference_culled = 1;
			greatest_gap = fmaxf(greatest_gap, gap);
		}
		showbbox.value = iteration & 1;
		int culled = R_CullAliasModel(&model, actual, &e);
		assert(!culled || reference_culled); /* Never introduce missing geometry. */
		assert(culled || greatest_gap < .1f); /* Only roundoff can retain an extra box. */
		if (!culled && showbbox.value)
			for (int p = 0; p < 8; p++)
				for (int axis = 0; axis < 3; axis++)
					assert(fabsf(corners[p][axis] - actual[p][axis]) <=
						4 * FLT_EPSILON * (fabsf(e.origin[axis]) + 2000));
	}
}
static void check_vectors(void)
{
	for (int iteration = 0; iteration < 10000; iteration++)
	{
		vec3_t angles, expected[3], actual[3];
		for (int i = 0; i < 3; i++) angles[i] = random_float(-360, 360);
		if (iteration % 3) angles[PITCH] = angles[ROLL] = 0;
		if (iteration % 9 == 0) VectorClear(angles);
		AngleVectors(angles, expected[0], expected[1], expected[2]);
		R_AliasVectors(angles, actual[0], actual[1], actual[2]);
		for (int i = 0; i < 3; i++)
			for (int j = 0; j < 3; j++)
				assert(expected[i][j] == actual[i][j]);
	}
}
int main(void)
{
	for (int i = 0; i < 256; i++) { gammatable[i] = (byte)(255 - i); minlight[i] = (byte)(128 + i / 2); }
	dmdl_t *h = new_model();
	check_vectors(); check_submission(h); check_interpolation_bounds(); check_culling(h); free(h);
	puts("PASS: MD2 direct/cached/fallback parity; colour generation wrap; 32000 interpolation bounds and all shell combinations; 100000 conservative culling cases");
}
