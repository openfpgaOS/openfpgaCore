/* Native meshlet order/seams, capacity, actual animation bounds and ownership. */
#include <assert.h>
#include "../../src/quake2/src/client/refresh/gl1/gl1_aliascache.c"

static void check(unsigned vertices, unsigned frames)
{
	unsigned xyz = vertices > 40 ? vertices - 7 : vertices;
	unsigned commands = 3 * vertices + 2;
	size_t size = sizeof(dmdl_t) + frames * (40 + xyz * 4) + commands * 4;
	dmdl_t *h = calloc(1, size);
	assert(h);
	h->num_xyz = xyz; h->num_frames = frames; h->num_glcmds = commands;
	h->framesize = 40 + xyz * 4; h->ofs_frames = sizeof(*h);
	h->ofs_glcmds = h->ofs_frames + frames * h->framesize; h->ofs_end = size;
	int *cmd = (int *)((byte *)h + h->ofs_glcmds);
	cmd[0] = vertices;
	for (unsigned v = 0; v < vertices; ++v)
	{
		float s = (float)(v % 17) / 16, t = (float)(v / 17) / 16;
		memcpy(cmd + 1 + v * 3, &s, 4); memcpy(cmd + 2 + v * 3, &t, 4);
		cmd[3 + v * 3] = v % xyz;
	}
	for (unsigned f = 0; f < frames; ++f)
	{
		daliasframe_t *frame = (daliasframe_t *)((byte *)h + h->ofs_frames + f * h->framesize);
		for (unsigned v = 0; v < xyz; ++v)
		{
			frame->verts[v].v[0] = 17 + (v % 61);
			frame->verts[v].v[1] = 43 + ((v + f) % 89);
			frame->verts[v].v[2] = 71 + ((v * 3 + f) % 67);
			frame->verts[v].lightnormalindex = (v * 17 + f) & 255;
		}
	}
	of_alias_mesh_t *mesh = R_BuildAliasMesh(h);
	assert(mesh && !mesh->native);
	R_BuildAliasNative(mesh, h);
	ofgl_native_mesh_t *n = mesh->native;
	assert(n && n->frame_count == frames && n->source_vertices == xyz);
	assert(n->gpu_generation == 0 && n->bytes == alias_native_bytes);
	const unsigned short *idx = (const unsigned short *)(mesh->vertices + mesh->numverts);
	uint32_t *word = n->blocks;
	unsigned tri = 0;
	for (unsigned b = 0; b < n->block_count; ++b)
	{
		unsigned nv = *word & 255, nt = (*word++ >> 8) & 65535;
		assert(nv && nv <= 32 && nt && nt <= 64);
		uint32_t *verts = word; word += nv * 3;
		for (unsigned t = 0; t < nt; ++t, ++tri)
		{
			uint32_t packed = *word++;
			assert(!(packed & ~32767u));
			for (unsigned k = 0; k < 3; ++k)
			{
				unsigned local = (packed >> (k * 5)) & 31;
				const of_alias_vertex_t *expected = &mesh->vertices[idx[tri * 3 + k]];
				assert(local < nv && verts[local * 3] == (unsigned)expected->xyz);
				assert(!memcmp(verts + local * 3 + 1, &expected->s, 8));
			}
		}
	}
	assert(tri * 3 == (unsigned)mesh->numindices && word == n->blocks + n->block_words);
	for (unsigned f = 0; f < frames; ++f)
	{
		unsigned lo[3] = {255,255,255}, hi[3] = {0}, mask[8] = {0};
		const uint8_t *frame = n->frames + f * n->frame_stride;
		for (int i = 0; i < mesh->numverts; ++i)
		{
			const uint8_t *v = frame + mesh->vertices[i].xyz * 4;
			for (unsigned k = 0; k < 3; ++k)
			{
				if (v[k] < lo[k]) lo[k] = v[k];
				if (v[k] > hi[k]) hi[k] = v[k];
			}
			mask[v[3] >> 5] |= 1u << (v[3] & 31);
		}
		for (unsigned k = 0; k < 3; ++k)
			assert(n->bounds[f * 6 + k] == lo[k] && n->bounds[f * 6 + k + 3] == hi[k]);
		assert(!memcmp(mask, n->normal_masks + f * 8, sizeof(mask)));
	}
	R_BuildAliasNative(mesh, h); assert(mesh->native == n); /* immutable cache */
	R_FreeAliasMesh(mesh); assert(!alias_mesh_bytes && !alias_native_bytes);
	mesh = R_BuildAliasMesh(h); assert(mesh);
	h->ofs_end = h->ofs_frames + frames * h->framesize - 1;
	R_BuildAliasNative(mesh, h); assert(!mesh->native);
	R_FreeAliasMesh(mesh); free(h);
}

int main(void)
{
	check(3, 1); check(31, 2); check(80, 7); check(257, 32);
	puts("PASS: native MD2 meshlets preserve ordered triangles/UV seams, 32-vertex bounds, native frames, exact bounds/normal masks, invalid extents and release");
}
