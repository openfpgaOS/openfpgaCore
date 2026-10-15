/* SPDX-License-Identifier: GPL-2.0-or-later
 * Compile immutable MD2 strip/fan topology once. UV seams remain distinct;
 * identical (position index, S, T) vertices share animation and GPU work.
 */
#include "header/local.h"

#ifdef YQ2_OPENFPGA
#define ALIAS_MESH_BUDGET (512u * 1024u)
static size_t alias_mesh_bytes;

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
	alias_mesh_bytes -= mesh->bytes;
	free(mesh);
}
#endif
