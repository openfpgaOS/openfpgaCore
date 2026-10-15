/*
 * Copyright (C) 1997-2001 Id Software, Inc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or (at
 * your option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 *
 * See the GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA
 * 02111-1307, USA.
 *
 * =======================================================================
 *
 * Mesh handling
 *
 * =======================================================================
 */

#include "header/local.h"
#include <float.h>

#define NUMVERTEXNORMALS 162
#define SHADEDOT_QUANT 16

float r_avertexnormals[NUMVERTEXNORMALS][3] = {
#include "../constants/anorms.h"
};

/* precalculated dot products for quantized angles */
float r_avertexnormal_dots[SHADEDOT_QUANT][256] = {
#include "../constants/anormtab.h"
};

typedef float vec4_t[4];
static vec4_t s_lerped[MAX_VERTS];
vec3_t shadevector;
float shadelight[3];
float *shadedots = r_avertexnormal_dots[0];
extern vec3_t lightspot;

extern unsigned char minlight[256];

static void
R_AliasVectors(const vec3_t angles, vec3_t forward, vec3_t right, vec3_t up)
{
	/* Items and most actors only turn around the vertical axis. Avoid the
	 * four pitch/roll trig calls and the general matrix products for them. */
	if (angles[PITCH] == 0 && angles[ROLL] == 0)
	{
		float sine = 0, cosine = 1;
		if (angles[YAW] != 0)
		{
			float angle = angles[YAW] * (M_PI * 2 / 360);
			sine = sinf(angle);
			cosine = cosf(angle);
		}
		VectorSet(forward, cosine, sine, 0);
		VectorSet(right, sine, -cosine, 0);
		VectorSet(up, 0, 0, 1);
		return;
	}
	AngleVectors(angles, forward, right, up);
}

static void
R_LerpVerts(entity_t *currententity, int nverts, dtrivertx_t *v, dtrivertx_t *ov,
		dtrivertx_t *verts, float *lerp, float move[3],
		float frontv[3], float backv[3])
{
	int i;

	if (currententity->flags &
		(RF_SHELL_RED | RF_SHELL_GREEN |
		 RF_SHELL_BLUE | RF_SHELL_DOUBLE |
		 RF_SHELL_HALF_DAM))
	{
		for (i = 0; i < nverts; i++, v++, ov++, lerp += 4)
		{
			float *normal = r_avertexnormals[verts[i].lightnormalindex];

			lerp[0] = move[0] + ov->v[0] * backv[0] + v->v[0] * frontv[0] +
					  normal[0] * POWERSUIT_SCALE;
			lerp[1] = move[1] + ov->v[1] * backv[1] + v->v[1] * frontv[1] +
					  normal[1] * POWERSUIT_SCALE;
			lerp[2] = move[2] + ov->v[2] * backv[2] + v->v[2] * frontv[2] +
					  normal[2] * POWERSUIT_SCALE;
		}
	}
	else
	{
		for (i = 0; i < nverts; i++, v++, ov++, lerp += 4)
		{
			lerp[0] = move[0] + ov->v[0] * backv[0] + v->v[0] * frontv[0];
			lerp[1] = move[1] + ov->v[1] * backv[1] + v->v[1] * frontv[1];
			lerp[2] = move[2] + ov->v[2] * backv[2] + v->v[2] * frontv[2];
		}
	}
}

#ifdef YQ2_OPENFPGA
static void
R_SetAliasDrawBounds(const entity_t *entity, const vec3_t move,
	const vec3_t frontv, const vec3_t backv)
{
	vec3_t mins, maxs;
	float shell = entity->flags & (RF_SHELL_RED | RF_SHELL_GREEN |
		RF_SHELL_BLUE | RF_SHELL_DOUBLE | RF_SHELL_HALF_DAM) ? POWERSUIT_SCALE : 0;
	for (int i = 0; i < 3; i++)
	{
		/* MD2 position components are unsigned bytes. Bound the actual
		 * interpolation, including translated origins, negative scales and
		 * extrapolation. Shell normals have components in [-1,1]. */
		float front = 255.0f * frontv[i], back = 255.0f * backv[i];
		float margin = shell + 0.125f +
			(fabsf(move[i]) + fabsf(front) + fabsf(back)) * 0.00001f;
		mins[i] = move[i] + fminf(front, 0) + fminf(back, 0) - margin;
		maxs[i] = move[i] + fmaxf(front, 0) + fmaxf(back, 0) + margin;
		/* Bit tests remain valid in the fast-math target build. Invalid
		 * metadata must leave the renderer on its ordinary clipping path. */
		uint32_t lo, hi;
		memcpy(&lo, &mins[i], sizeof(lo));
		memcpy(&hi, &maxs[i], sizeof(hi));
		if ((lo & 0x7f800000u) == 0x7f800000u ||
			(hi & 0x7f800000u) == 0x7f800000u)
		{
			OFGL_SetDrawBounds(NULL, NULL);
			return;
		}
	}
	OFGL_SetDrawBounds(mins, maxs);
}
#endif

/*
 * Interpolates between two frames and origins
 */
static void
R_DrawAliasFrameLerp(entity_t *currententity, const model_t *model, dmdl_t *paliashdr, float backlerp)
{
	daliasframe_t *frame, *oldframe;
	dtrivertx_t *v, *ov, *verts;
	int *order;
	int count, i, index_xyz;
	float tex[2], frontlerp, l, alpha;
	vec3_t move, delta, vectors[3];
	vec3_t frontv, backv;
	float *lerp;

	frame = (daliasframe_t *)((byte *)paliashdr + paliashdr->ofs_frames
							  + currententity->frame * paliashdr->framesize);
	verts = v = frame->verts;

	oldframe = (daliasframe_t *)((byte *)paliashdr + paliashdr->ofs_frames
				+ currententity->oldframe * paliashdr->framesize);
	ov = oldframe->verts;

	order = (int *)((byte *)paliashdr + paliashdr->ofs_glcmds);

	if (currententity->flags & RF_TRANSLUCENT)
	{
		alpha = currententity->alpha;
	}
	else
	{
		alpha = 1.0;
	}

	frontlerp = 1.0 - backlerp;

	/* move should be the delta back to the previous frame * backlerp */
	VectorSubtract(currententity->oldorigin, currententity->origin, delta);
	if (delta[0] == 0.0f && delta[1] == 0.0f && delta[2] == 0.0f)
	{
		/* Animation can change while the entity stays in place. A zero
		 * translation needs no trigonometry or basis transformation. */
		VectorCopy(oldframe->translate, move);
	}
	else
	{
		R_AliasVectors(currententity->angles, vectors[0], vectors[1], vectors[2]);
		move[0] = DotProduct(delta, vectors[0]); /* forward */
		move[1] = -DotProduct(delta, vectors[1]); /* left */
		move[2] = DotProduct(delta, vectors[2]); /* up */
		VectorAdd(move, oldframe->translate, move);
	}

	for (i = 0; i < 3; i++)
	{
		move[i] = backlerp * move[i] + frontlerp * frame->translate[i];
	}

	for (i = 0; i < 3; i++)
	{
		frontv[i] = frontlerp * frame->scale[i];
		backv[i] = backlerp * oldframe->scale[i];
	}

#ifdef YQ2_OPENFPGA
	if (model->of_alias && model->of_alias->native && OFGL_NativeAvailable() &&
		!(currententity->flags & (RF_TRANSLUCENT | RF_WEAPONMODEL | RF_DEPTHHACK |
		RF_SHELL_RED | RF_SHELL_GREEN | RF_SHELL_BLUE | RF_SHELL_DOUBLE | RF_SHELL_HALF_DAM)) &&
		(!gl_shadows->value || (currententity->flags & RF_NOSHADOW)))
	{
		ofgl_native_draw_t draw = {0};
		ofgl_native_mesh_t *native = model->of_alias->native;
		draw.mesh = native;
		draw.frame = currententity->frame;
		draw.old_frame = currententity->oldframe;
		const uint8_t *bound = native->bounds + draw.frame * 6;
		const uint8_t *oldbound = native->bounds + draw.old_frame * 6;
		for (i = 0; i < 3; ++i)
		{
			draw.move[i] = move[i];
			draw.scale[i] = frontv[i];
			draw.old_scale[i] = backv[i];
			float a = frontv[i] * bound[i], b = frontv[i] * bound[i + 3];
			float c = backv[i] * oldbound[i], d = backv[i] * oldbound[i + 3];
			float margin = 0.125f + (fabsf(move[i]) + fabsf(a) + fabsf(b) + fabsf(c) + fabsf(d)) * 0.00001f;
			draw.mins[i] = move[i] + fminf(a, b) + fminf(c, d) - margin;
			draw.maxs[i] = move[i] + fmaxf(a, b) + fmaxf(c, d) + margin;
		}
		/* Match the existing normal-light/gamma/minlight calculation exactly,
		 * once per referenced normal instead of writing a colour for each seam.
		 * The palette is copied into the job before this stack frame returns. */
		for (unsigned group = 0; group < 8; ++group)
		{
			uint32_t mask = native->normal_masks[draw.frame * 8 + group];
			while (mask)
			{
				unsigned normal = group * 32 + (unsigned)__builtin_ctz(mask);
				mask &= mask - 1;
				int idx[3];
				float dot = shadedots[normal];
				for (unsigned k = 0; k < 3; ++k)
				{
					idx[k] = dot * shadelight[k] * 255;
					idx[k] = Q_clamp(idx[k], 0, 255);
					if (gl_state.minlight_set) idx[k] = minlight[idx[k]];
				}
				uint32_t rgb = ((gammatable[idx[0]] & 0xf8u) << 8) |
					((gammatable[idx[1]] & 0xfcu) << 3) | (gammatable[idx[2]] >> 3);
				draw.palette[normal >> 1] |= rgb << ((normal & 1u) * 16u);
			}
		}
		if (R_DrawNativeAlias(&draw)) return;
	}
#endif

	lerp = s_lerped[0];

	R_LerpVerts(currententity, paliashdr->num_xyz, v, ov, verts, lerp, move, frontv, backv);

#ifdef YQ2_OPENFPGA
	if (model->of_alias)
	{
		const of_alias_mesh_t *mesh = model->of_alias;
		int shell = currententity->flags & (RF_SHELL_RED | RF_SHELL_GREEN | RF_SHELL_BLUE);
		/* An MD2 normal has one colour for this draw. Cache the full gamma /
		 * minlight result instead of recomputing it for every vertex and seam. */
		static uint32_t colours[256];
		static uint16_t colour_tag[256], colour_generation;
		if (++colour_generation == 0)
		{
			memset(colour_tag, 0, sizeof(colour_tag));
			colour_generation = 1;
		}
		uint32_t alpha_byte = (byte)(alpha * 255);
		int direct = R_BufferAliasDirect(mesh);
		if (!direct) R_BufferAliasIndices(mesh);
		int source_positions = direct && R_BufferAliasPositions(s_lerped[0]);
		float *positions = gl_buf.vtx + gl_buf.vt;
		float *texcoords = gl_buf.tex[0] + gl_buf.tx;
		byte *colours_out = gl_buf.clr + gl_buf.cl;
		for (int k = 0; k < mesh->numverts; k++)
		{
			const of_alias_vertex_t *entry = &mesh->vertices[k];
			index_xyz = entry->xyz;
			if (!source_positions)
			{
				memcpy(positions, s_lerped[index_xyz], 3 * sizeof(float));
				positions += 3;
			}
			if (!shell && !direct)
			{
				texcoords[0] = entry->s;
				texcoords[1] = entry->t;
				texcoords += 2;
			}
			unsigned normal = shell ? 0 : verts[index_xyz].lightnormalindex;
			if (colour_tag[normal] != colour_generation)
			{
				int idx[3];
				l = shell ? 1.0f : shadedots[normal];
				for (i = 0; i < 3; i++)
				{
					idx[i] = l * shadelight[i] * 255;
					idx[i] = Q_clamp(idx[i], 0, 255);
					if (gl_state.minlight_set) idx[i] = minlight[idx[i]];
				}
				colours[normal] = gammatable[idx[0]] | ((uint32_t)gammatable[idx[1]] << 8) |
					((uint32_t)gammatable[idx[2]] << 16) | (alpha_byte << 24);
				colour_tag[normal] = colour_generation;
			}
			/* The OpenFPGA target and GL_UNSIGNED_BYTE arrays are LE.
			 * memcpy also avoids aliasing the byte array through uint32_t. */
			memcpy(colours_out, &colours[normal], sizeof(uint32_t));
			colours_out += 4;
		}
		gl_buf.vt += mesh->numverts * 3;
		if (!shell && !direct) gl_buf.tx += mesh->numverts * 2;
		gl_buf.cl += mesh->numverts * 4;
		R_SetAliasDrawBounds(currententity, move, frontv, backv);
		return;
	}
#endif

	while (1)
	{
		int idx[3];

		/* get the vertex count and primitive type */
		count = *order++;

		if (!count)
		{
			break; /* done */
		}

		if (count < 0)
		{
			count = -count;
			R_SetBufferIndices(GL_TRIANGLE_FAN, count);
		}
		else
		{
			R_SetBufferIndices(GL_TRIANGLE_STRIP, count);
		}

		if (currententity->flags &
			(RF_SHELL_RED | RF_SHELL_GREEN | RF_SHELL_BLUE))
		{
			do
			{
				index_xyz = order[2];
				order += 3;

				GLBUFFER_VERTEX(s_lerped[index_xyz][0],
					s_lerped[index_xyz][1], s_lerped[index_xyz][2])

				for (i = 0; i < 3; i++)
				{
					idx[i] = shadelight[i] * 255;
					idx[i] = Q_clamp(idx[i], 0, 255);
				}

				if (gl_state.minlight_set)
				{
					for (i = 0; i < 3; i++)
					{
						idx[i] = minlight[idx[i]];
					}
				}

				GLBUFFER_COLOR(idx[0], idx[1], idx[2], alpha * 255)
			}
			while (--count);
		}
		else
		{
			do
			{
				/* texture coordinates come from the draw list */
				tex[0] = ((float *)order)[0];
				tex[1] = ((float *)order)[1];

				index_xyz = order[2];
				order += 3;

				/* normals and vertexes come from the frame list */
				l = shadedots[verts[index_xyz].lightnormalindex];

				GLBUFFER_VERTEX(s_lerped[index_xyz][0],
					s_lerped[index_xyz][1], s_lerped[index_xyz][2])

				GLBUFFER_SINGLETEX(tex[0], tex[1])

				for (i = 0; i < 3; i++)
				{
					idx[i] = l * shadelight[i] * 255;
					idx[i] = Q_clamp(idx[i], 0, 255);
				}

				if (gl_state.minlight_set)
				{
					for (i = 0; i < 3; i++)
					{
						idx[i] = minlight[idx[i]];
					}
				}

				GLBUFFER_COLOR(idx[0], idx[1], idx[2], alpha * 255)
			}
			while (--count);
		}
	}
#ifdef YQ2_OPENFPGA
	R_SetAliasDrawBounds(currententity, move, frontv, backv);
#endif
}

static void
R_DrawAliasShadow(entity_t *currententity, dmdl_t *paliashdr, int posenum)
{
	// Don't do stencil test on unsupported stereo modes
	const qboolean stencilt = ( gl_state.stencil && gl1_stencilshadow->value &&
		( gl_state.stereo_mode < STEREO_MODE_ROW_INTERLEAVED
		|| gl_state.stereo_mode > STEREO_MODE_PIXEL_INTERLEAVED ) );

	int *order;
	vec3_t point;
	float height = 0, lheight;
	int count;

	lheight = currententity->origin[2] - lightspot[2];
	order = (int *)((byte *)paliashdr + paliashdr->ofs_glcmds);
	height = -lheight + 0.1f;

	R_UpdateGLBuffer(buf_shadow, 0, 0, 0, 1);

	/* stencilbuffer shadows */
	if (stencilt)
	{
		glEnable(GL_STENCIL_TEST);
		glStencilFunc(GL_EQUAL, 1, 2);
		glStencilOp(GL_KEEP, GL_KEEP, GL_INCR);
	}

	while (1)
	{
		/* get the vertex count and primitive type */
		count = *order++;

		if (!count)
		{
			break; /* done */
		}

		if (count < 0)
		{
			count = -count;
			R_SetBufferIndices(GL_TRIANGLE_FAN, count);
		}
		else
		{
			R_SetBufferIndices(GL_TRIANGLE_STRIP, count);
		}

		do
		{
			/* normals and vertexes come from the frame list */
			memcpy(point, s_lerped[order[2]], sizeof(point));

			point[0] -= shadevector[0] * (point[2] + lheight);
			point[1] -= shadevector[1] * (point[2] + lheight);
			point[2] = height;

			GLBUFFER_VERTEX( point[0], point[1], point[2] )

			order += 3;
		}
		while (--count);
	}

	R_ApplyGLBuffer();

	/* stencilbuffer shadows */
	if (stencilt)
	{
		glDisable(GL_STENCIL_TEST);
	}
}

static qboolean
R_CullAliasModel(const model_t *currentmodel, vec3_t bbox[8], entity_t *e)
{
	int i;
	vec3_t mins, maxs;
	dmdl_t *paliashdr;
	vec3_t vectors[3];
	vec3_t thismins, oldmins, thismaxs, oldmaxs;
	daliasframe_t *pframe, *poldframe;
	vec3_t angles;

	paliashdr = (dmdl_t *)currentmodel->extradata;

	if ((e->frame >= paliashdr->num_frames) || (e->frame < 0))
	{
		Com_DPrintf("%s %s: no such frame %d\n", __func__,
				currentmodel->name, e->frame);
		e->frame = 0;
	}

	if ((e->oldframe >= paliashdr->num_frames) || (e->oldframe < 0))
	{
		Com_DPrintf("%s %s: no such oldframe %d\n", __func__,
				currentmodel->name, e->oldframe);
		e->oldframe = 0;
	}

	pframe = (daliasframe_t *)((byte *)paliashdr + paliashdr->ofs_frames +
			e->frame * paliashdr->framesize);

	poldframe = (daliasframe_t *)((byte *)paliashdr + paliashdr->ofs_frames +
			e->oldframe * paliashdr->framesize);

	/* compute axially aligned mins and maxs */
	if (pframe == poldframe)
	{
		for (i = 0; i < 3; i++)
		{
			mins[i] = pframe->translate[i];
			maxs[i] = mins[i] + pframe->scale[i] * 255;
		}
	}
	else
	{
		for (i = 0; i < 3; i++)
		{
			thismins[i] = pframe->translate[i];
			thismaxs[i] = thismins[i] + pframe->scale[i] * 255;

			oldmins[i] = poldframe->translate[i];
			oldmaxs[i] = oldmins[i] + poldframe->scale[i] * 255;

			if (thismins[i] < oldmins[i])
			{
				mins[i] = thismins[i];
			}
			else
			{
				mins[i] = oldmins[i];
			}

			if (thismaxs[i] > oldmaxs[i])
			{
				maxs[i] = thismaxs[i];
			}
			else
			{
				maxs[i] = oldmaxs[i];
			}
		}
	}

	/* Rotate each frustum normal into model space and test the corner
	 * furthest along it. This is the same box/plane test as evaluating all
	 * eight rotated corners, without constructing them for every model. */
	VectorCopy(e->angles, angles);
	angles[YAW] = -angles[YAW];
	R_AliasVectors(angles, vectors[0], vectors[1], vectors[2]);
	vec3_t support_mins, support_maxs;
	for (i = 0; i < 3; i++)
	{
		/* A negative MD2 scale reverses the endpoints. The original eight
		 * corners cover either ordering; preserve that behavior here while
		 * retaining the original endpoints for debug box vertex ordering. */
		support_mins[i] = fminf(mins[i], maxs[i]);
		support_maxs[i] = fmaxf(mins[i], maxs[i]);
	}
	for (int f = 0; f < 4; f++)
	{
		vec3_t normal;
		float support = 0, magnitude = fabsf(frustum[f].dist) + 1.0f;
		for (i = 0; i < 3; i++)
		{
			normal[i] = frustum[f].normal[0] * vectors[0][i] -
				frustum[f].normal[1] * vectors[1][i] +
				frustum[f].normal[2] * vectors[2][i];
			float term = normal[i] * (normal[i] < 0 ? support_mins[i] : support_maxs[i]);
			float origin = frustum[f].normal[i] * e->origin[i];
			support += term + origin;
			/* Include the unreduced bounds, since a rotated normal may
			 * contain cancelling terms. Keep rounding at a clip boundary
			 * conservative: retaining an extra model is harmless. */
			magnitude += fabsf(mins[i]) + fabsf(maxs[i]) + fabsf(origin);
		}
		if (support - frustum[f].dist < -16.0f * FLT_EPSILON * magnitude)
			return true;
	}

	if (!gl_showbbox->value)
		return false;

	/* Debug drawing is the only consumer of the explicit world corners. */
	for (i = 0; i < 8; i++)
	{
		vec3_t tmp;

		if (i & 1)
		{
			tmp[0] = mins[0];
		}
		else
		{
			tmp[0] = maxs[0];
		}

		if (i & 2)
		{
			tmp[1] = mins[1];
		}
		else
		{
			tmp[1] = maxs[1];
		}

		if (i & 4)
		{
			tmp[2] = mins[2];
		}
		else
		{
			tmp[2] = maxs[2];
		}

		VectorCopy(tmp, bbox[i]);
	}

	for (i = 0; i < 8; i++)
	{
		vec3_t tmp;

		VectorCopy(bbox[i], tmp);

		bbox[i][0] = DotProduct(vectors[0], tmp);
		bbox[i][1] = -DotProduct(vectors[1], tmp);
		bbox[i][2] = DotProduct(vectors[2], tmp);

		VectorAdd(e->origin, bbox[i], bbox[i]);
	}

	return false;
}

void
R_DrawAliasModel(entity_t *currententity, const model_t *currentmodel)
{
	int i;
	dmdl_t *paliashdr;
	float an;
	vec3_t bbox[8];
	image_t *skin;

	if (!(currententity->flags & RF_WEAPONMODEL))
	{
		if (R_CullAliasModel(currentmodel, bbox, currententity))
		{
			return;
		}
	}

	if (currententity->flags & RF_WEAPONMODEL)
	{
		if (gl_lefthand->value == 2)
		{
			return;
		}
	}

	paliashdr = (dmdl_t *)currentmodel->extradata;

	/* get lighting information */
	if (currententity->flags &
		(RF_SHELL_HALF_DAM | RF_SHELL_GREEN | RF_SHELL_RED |
		 RF_SHELL_BLUE | RF_SHELL_DOUBLE))
	{
		VectorClear(shadelight);

		if (currententity->flags & RF_SHELL_HALF_DAM)
		{
			shadelight[0] = 0.56;
			shadelight[1] = 0.59;
			shadelight[2] = 0.45;
		}

		if (currententity->flags & RF_SHELL_DOUBLE)
		{
			shadelight[0] = 0.9;
			shadelight[1] = 0.7;
		}

		if (currententity->flags & RF_SHELL_RED)
		{
			shadelight[0] = 1.0;
		}

		if (currententity->flags & RF_SHELL_GREEN)
		{
			shadelight[1] = 1.0;
		}

		if (currententity->flags & RF_SHELL_BLUE)
		{
			shadelight[2] = 1.0;
		}
	}
	else if (currententity->flags & RF_FULLBRIGHT)
	{
		for (i = 0; i < 3; i++)
		{
			shadelight[i] = 1.0;
		}
	}
	else
	{
		R_LightPoint(currententity, currententity->origin, shadelight);

		/* player lighting hack for communication back to server */
		if (currententity->flags & RF_WEAPONMODEL)
		{
			/* pick the greatest component, which should be
			   the same as the mono value returned by software */
			if (shadelight[0] > shadelight[1])
			{
				if (shadelight[0] > shadelight[2])
				{
					r_lightlevel->value = 150 * shadelight[0];
				}
				else
				{
					r_lightlevel->value = 150 * shadelight[2];
				}
			}
			else
			{
				if (shadelight[1] > shadelight[2])
				{
					r_lightlevel->value = 150 * shadelight[1];
				}
				else
				{
					r_lightlevel->value = 150 * shadelight[2];
				}
			}
		}
	}

	if (currententity->flags & RF_MINLIGHT)
	{
		for (i = 0; i < 3; i++)
		{
			if (shadelight[i] > 0.1)
			{
				break;
			}
		}

		if (i == 3)
		{
			shadelight[0] = 0.1;
			shadelight[1] = 0.1;
			shadelight[2] = 0.1;
		}
	}

	if (currententity->flags & RF_GLOW)
	{
		/* bonus items will pulse with time */
		float scale;
		float min;

		scale = 0.1 * sin(r_newrefdef.time * 7);

		for (i = 0; i < 3; i++)
		{
			min = shadelight[i] * 0.8;
			shadelight[i] += scale;

			if (shadelight[i] < min)
			{
				shadelight[i] = min;
			}
		}
	}


    // Apply gl1_overbrightbits to the mesh. If we don't do this they will appear slightly dimmer relative to walls.
    if (gl1_overbrightbits->value)
    {
        for (i = 0; i < 3; ++i)
        {
            shadelight[i] *= gl1_overbrightbits->value;
        }
    }


	/* ir goggles color override */
	if (r_newrefdef.rdflags & RDF_IRGOGGLES && currententity->flags &
		RF_IR_VISIBLE)
	{
		shadelight[0] = 1.0;
		shadelight[1] = 0.0;
		shadelight[2] = 0.0;
	}

	shadedots = r_avertexnormal_dots[((int)(currententity->angles[1] *
				(SHADEDOT_QUANT / 360.0))) & (SHADEDOT_QUANT - 1)];

	/* locate the proper data */
	c_alias_polys += paliashdr->num_tris;

	glPushMatrix();
	currententity->angles[PITCH] = -currententity->angles[PITCH];
	R_RotateForEntity(currententity);
	currententity->angles[PITCH] = -currententity->angles[PITCH];

	/* select skin */
	if (currententity->skin)
	{
		skin = currententity->skin; /* custom player skin */
	}
	else
	{
		if (currententity->skinnum >= MAX_MD2SKINS)
		{
			skin = currentmodel->skins[0];
		}
		else
		{
			skin = currentmodel->skins[currententity->skinnum];

			if (!skin)
			{
				skin = currentmodel->skins[0];
			}
		}
	}

	if (!skin)
	{
		skin = r_notexture; /* fallback... */
	}

	if ((currententity->frame >= paliashdr->num_frames) ||
		(currententity->frame < 0))
	{
		Com_DPrintf("%s %s: no such frame %d\n", __func__,
				currentmodel->name, currententity->frame);
		currententity->frame = 0;
		currententity->oldframe = 0;
	}

	if ((currententity->oldframe >= paliashdr->num_frames) ||
		(currententity->oldframe < 0))
	{
		Com_DPrintf("%s %s: no such oldframe %d\n", __func__,
				currentmodel->name, currententity->oldframe);
		currententity->frame = 0;
		currententity->oldframe = 0;
	}

	if (!r_lerpmodels->value)
	{
		currententity->backlerp = 0;
	}

	R_UpdateGLBuffer(buf_alias, skin->texnum, 0, currententity->flags, 1);
	R_DrawAliasFrameLerp(currententity, currentmodel, paliashdr, currententity->backlerp);
	R_ApplyGLBuffer();
#ifdef YQ2_OPENFPGA
	OFGL_SetDrawBounds(NULL, NULL);
#endif

	R_TexEnv(GL_REPLACE);
	glShadeModel(GL_FLAT);

	glPopMatrix();

	if (gl_showbbox->value && !(currententity->flags & RF_WEAPONMODEL))
	{
		glDisable(GL_CULL_FACE);
		glPolygonMode(GL_FRONT_AND_BACK, GL_LINE);
		glDisable(GL_TEXTURE_2D);

		glEnableClientState(GL_VERTEX_ARRAY);
		glVertexPointer(3, GL_FLOAT, 0, bbox);
		glDrawArrays(GL_TRIANGLE_STRIP, 0, 8);
		glDisableClientState(GL_VERTEX_ARRAY);

		glEnable(GL_TEXTURE_2D);
		glPolygonMode(GL_FRONT_AND_BACK, GL_FILL);
		glEnable(GL_CULL_FACE);
	}

	if (gl_shadows->value &&
		!(currententity->flags & (RF_TRANSLUCENT | RF_WEAPONMODEL | RF_NOSHADOW)))
	{
		/* The projected shadow is the only consumer of this direction.
		 * Avoid two trigonometric calls and normalization for every model
		 * when shadows are disabled, or this entity does not cast one. */
		an = currententity->angles[1] / 180 * M_PI;
		shadevector[0] = cos(-an);
		shadevector[1] = sin(-an);
		shadevector[2] = 1;
		VectorNormalize(shadevector);

		glPushMatrix();

		/* don't rotate shadows on ungodly axes */
		glTranslatef(currententity->origin[0], currententity->origin[1], currententity->origin[2]);
		glRotatef(currententity->angles[1], 0, 0, 1);

		glDisable(GL_TEXTURE_2D);
		glEnable(GL_BLEND);
		glColor4f(0, 0, 0, 0.5f);
		R_DrawAliasShadow(currententity, paliashdr, currententity->frame);
		glEnable(GL_TEXTURE_2D);
		glDisable(GL_BLEND);
		glPopMatrix();
	}

	glColor4f(1, 1, 1, 1);
}
