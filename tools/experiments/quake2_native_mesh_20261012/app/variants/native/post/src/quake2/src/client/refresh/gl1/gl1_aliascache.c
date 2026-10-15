/* SPDX-License-Identifier: GPL-2.0-or-later
 * Compile immutable MD2 strip/fan topology once. UV seams remain distinct;
 * identical (position index, S, T) vertices share animation and GPU work.
 */
#include "header/local.h"

#ifdef YQ2_OPENFPGA
#define ALIAS_MESH_BUDGET (512u * 1024u)
static size_t alias_mesh_bytes;
#define ALIAS_NATIVE_BUDGET (2u * 1024u * 1024u)
static size_t alias_native_bytes;

of_alias_mesh_t *
R_BuildAliasMesh(const dmdl_t *model)
{
	if (!model || model->num_xyz < 1 || model->num_xyz > MAX_VERTS || model->num_glcmds < 1 ||
		model->num_glcmds > MAX_INDICES || alias_mesh_bytes >= ALIAS_MESH_BUDGET)
		return NULL;
	int capacity = model->num_glcmds / 3;
	if (capacity < 3 || capacity >= MAX_VERTICES) return NULL;
	size_t bytes = sizeof(of_alias_mesh_t) + capacity * (sizeof(of_alias_vertex_t) + 6);
	of_alias_mesh_t *mesh = malloc(bytes);
	int *links = malloc((model->num_xyz + capacity) * sizeof(int));
	if (!mesh || !links)
	{
		free(mesh); free(links);
		return NULL;
	}
	int *heads = links, *next = links + model->num_xyz;
	for (int i = 0; i < model->num_xyz; i++) heads[i] = -1;
	mesh->numverts = mesh->numindices = 0;
	mesh->native = NULL;
	unsigned short *indices = (unsigned short *)(mesh->vertices + capacity);
	const int *cmd = (const int *)((const byte *)model + model->ofs_glcmds);
	const int *end = cmd + model->num_glcmds;
	int terminated = 0;
	while (cmd < end)
	{
		int count = *cmd++, fan = count < 0;
		if (!count) { terminated = 1; break; }
		/* Check before negation, including INT_MIN and truncated commands. */
		if (count < -capacity || count > capacity) goto fail;
		if (fan) count = -count;
		if (count < 3 || count > (end - cmd) / 3 ||
			mesh->numindices + (count - 2) * 3 > capacity * 3) goto fail;
		int first = 0, prev0 = 0, prev1 = 0;
		for (int i = 0; i < count; i++, cmd += 3)
		{
			int xyz = cmd[2], index;
			if (xyz < 0 || xyz >= model->num_xyz) goto fail;
			for (index = heads[xyz]; index >= 0; index = next[index])
			{
				/* Compare bits: preserve texture seams and signed-zero inputs. */
				if (!memcmp(&mesh->vertices[index].s, cmd, 2 * sizeof(float))) break;
			}
			if (index < 0)
			{
				if (mesh->numverts == capacity) goto fail;
				index = mesh->numverts++;
				mesh->vertices[index].xyz = xyz;
				memcpy(&mesh->vertices[index].s, cmd, 2 * sizeof(float));
				next[index] = heads[xyz]; heads[xyz] = index;
			}
			if (!i) first = index;
			if (i >= 2)
			{
				if (fan)
				{
					indices[mesh->numindices++] = first;
					indices[mesh->numindices++] = prev1;
					indices[mesh->numindices++] = index;
				}
				else
				{
					/* Keep the original strip's triangle order and winding. */
					indices[mesh->numindices++] = (i & 1) ? index : prev0;
					indices[mesh->numindices++] = prev1;
					indices[mesh->numindices++] = (i & 1) ? prev0 : index;
				}
			}
			prev0 = prev1; prev1 = index;
		}
	}
	if (!terminated || !mesh->numindices || mesh->numindices >= MAX_INDICES) goto fail;
	bytes = sizeof(*mesh) + mesh->numverts * sizeof(mesh->vertices[0]) + mesh->numindices * 2;
	if (bytes > ALIAS_MESH_BUDGET - alias_mesh_bytes) goto fail;
	memmove(mesh->vertices + mesh->numverts, indices, mesh->numindices * 2);
	/* Shrink away the construction capacity. A failed shrink leaves the
	 * original allocation valid, but discard it to keep the budget exact. */
	of_alias_mesh_t *small = realloc(mesh, bytes);
	if (!small) goto fail;
	free(links);
	small->bytes = bytes;
	alias_mesh_bytes += bytes;
	return small;
fail:
	free(links); free(mesh);
	return NULL;
}

void
R_FreeAliasMesh(of_alias_mesh_t *mesh)
{
	if (!mesh) return;
	if (mesh->native)
	{
		alias_native_bytes -= mesh->native->bytes;
		free(mesh->native);
	}
	alias_mesh_bytes -= mesh->bytes;
	free(mesh);
}

static uint32_t *
R_NativeBlock(uint32_t *out, const of_alias_mesh_t *mesh,
	const unsigned short slots[32], unsigned nv, const uint32_t tris[64], unsigned nt)
{
	*out++ = nv | (nt << 8);
	for (unsigned i = 0; i < nv; ++i)
	{
		const of_alias_vertex_t *v = &mesh->vertices[slots[i]];
		*out++ = (uint32_t)v->xyz;
		memcpy(out, &v->s, 2 * sizeof(float));
		out += 2;
	}
	memcpy(out, tris, nt * sizeof(*out));
	return out + nt;
}

/* Compile ordered meshlets once. UVs remain float bits in the CPU asset until
 * upload, when the actual power-of-two skin size determines their Q16 values.
 * Neither frames nor indices are re-read by the CPU in the native draw loop. */
void
R_BuildAliasNative(of_alias_mesh_t *mesh, const dmdl_t *model)
{
	if (!mesh || mesh->native || !model || model->num_frames < 1 ||
		model->num_frames > MAX_FRAMES || model->num_xyz < 1 ||
		model->num_xyz > MAX_VERTS || model->ofs_frames < 0 || model->ofs_end < 0 ||
		model->framesize < (int)offsetof(daliasframe_t, verts) + model->num_xyz * 4 ||
		(uint64_t)(unsigned)model->ofs_frames +
		(uint64_t)(unsigned)model->framesize * (unsigned)model->num_frames > (unsigned)model->ofs_end ||
		mesh->numindices < 3 || mesh->numindices % 3) return;
	unsigned ntri = (unsigned)mesh->numindices / 3;
	/* Every triangle has at most three new vertices and one block header. */
	uint32_t *scratch = malloc((size_t)ntri * 11 * sizeof(uint32_t));
	if (!scratch) return;
	const unsigned short *indices = (const unsigned short *)(mesh->vertices + mesh->numverts);
	unsigned short slots[32];
	uint32_t tris[64], *out = scratch;
	unsigned nv = 0, nt = 0, blocks = 0;
	for (unsigned t = 0; t < ntri; ++t)
	{
		unsigned ids[3], additional = 0;
		for (unsigned k = 0; k < 3; ++k)
		{
			unsigned id = indices[3 * t + k];
			if (id >= (unsigned)mesh->numverts) goto fail;
			unsigned j;
			for (j = 0; j < nv && slots[j] != id; ++j) { }
			if (j == nv)
			{
				unsigned prior;
				for (prior = 0; prior < k && indices[3 * t + prior] != id; ++prior) { }
				if (prior == k) ++additional;
			}
		}
		if (nt && (nv + additional > 32 || nt == 64))
		{
			out = R_NativeBlock(out, mesh, slots, nv, tris, nt);
			++blocks; nv = nt = 0;
		}
		for (unsigned k = 0; k < 3; ++k)
		{
			unsigned id = indices[3 * t + k], j;
			for (j = 0; j < nv && slots[j] != id; ++j) { }
			if (j == nv) slots[nv++] = id;
			ids[k] = j;
		}
		tris[nt++] = ids[0] | (ids[1] << 5) | (ids[2] << 10);
	}
	if (nt) { out = R_NativeBlock(out, mesh, slots, nv, tris, nt); ++blocks; }
	size_t words = (size_t)(out - scratch);
	size_t bytes = sizeof(ofgl_native_mesh_t) + words * 4 + (size_t)model->num_frames * 38;
	if (bytes > ALIAS_NATIVE_BUDGET - alias_native_bytes) goto fail;
	ofgl_native_mesh_t *native = calloc(1, bytes);
	if (!native) goto fail;
	native->bytes = bytes;
	native->blocks = (uint32_t *)(native + 1);
	native->block_words = words;
	native->block_count = blocks;
	native->source_vertices = model->num_xyz;
	native->frame_count = model->num_frames;
	native->frame_stride = model->framesize;
	native->frames = (const byte *)model + model->ofs_frames + offsetof(daliasframe_t, verts);
	native->normal_masks = native->blocks + words;
	native->bounds = (uint8_t *)(native->normal_masks + model->num_frames * 8);
	memcpy(native->blocks, scratch, words * 4);
	for (int v = 0; v < mesh->numverts; ++v)
	{
		uint32_t s, t;
		memcpy(&s, &mesh->vertices[v].s, 4);
		memcpy(&t, &mesh->vertices[v].t, 4);
		if ((s & 0x7f800000u) == 0x7f800000u || (t & 0x7f800000u) == 0x7f800000u ||
			mesh->vertices[v].xyz < 0 || mesh->vertices[v].xyz >= model->num_xyz)
		{ free(native); goto fail; }
		float as = fabsf(mesh->vertices[v].s), at = fabsf(mesh->vertices[v].t);
		if (as > native->max_s) native->max_s = as;
		if (at > native->max_t) native->max_t = at;
	}
	for (int f = 0; f < model->num_frames; ++f)
	{
		uint8_t *bound = native->bounds + f * 6;
		bound[0] = bound[1] = bound[2] = 255;
		for (int i = 0; i < mesh->numverts; ++i)
		{
			const uint8_t *v = native->frames + (size_t)f * native->frame_stride + mesh->vertices[i].xyz * 4;
			for (unsigned k = 0; k < 3; ++k)
			{
				if (v[k] < bound[k]) bound[k] = v[k];
				if (v[k] > bound[k + 3]) bound[k + 3] = v[k];
			}
			native->normal_masks[f * 8 + (v[3] >> 5)] |= 1u << (v[3] & 31);
		}
	}
	alias_native_bytes += bytes;
	mesh->native = native;
fail:
	free(scratch);
}
#endif
