/*
 * Copyright (C) 1997-2001 Id Software, Inc.
 * Copyright (C) 2024      Jaime Moreira
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
 * Drawing buffer: sort of a "Q3A shader" handler, allows to join multiple
 * draw calls into one, by grouping those which share the same
 * characteristics (mostly the same texture).
 *
 * =======================================================================
 */

#include "header/local.h"

#define GLBUFFER_RESET	vtx_ptr = idx_ptr = 0; gl_buf.vt = gl_buf.tx = gl_buf.cl = 0;

glbuffer_t gl_buf;	// our drawing buffer, used globally
int cur_lm_copy;	// which lightmap copy to use (when lightmapcopies=on)

static GLushort vtx_ptr, idx_ptr;	// pointers for array positions in gl_buf
#ifdef YQ2_OPENFPGA
static ofgl_world_poly_t *world_polys[128];
static int world_count;
static const of_alias_mesh_t *alias_direct;
static const float *alias_positions;

int
R_BufferWorldPoly(glpoly_t *poly)
{
	if (!poly->of_world || !OFGL_WorldPolysEnabled())
		return 0;
	if (idx_ptr || world_count == 128)
		R_ApplyGLBuffer();
	world_polys[world_count++] = poly->of_world;
	return 1;
}

/* Cached alias draws are flushed by R_DrawAliasModel before their model
 * or staging storage can change. Keep immutable indices and UVs in that model
 * when no earlier buffered geometry needs an index offset. */
int
R_BufferAliasDirect(const of_alias_mesh_t *mesh)
{
	if (!mesh || gl_buf.type != buf_alias || world_count || vtx_ptr || idx_ptr ||
		mesh->numverts < 1 || mesh->numverts >= MAX_VERTICES ||
		mesh->numindices < 1 || mesh->numindices >= MAX_INDICES)
		return 0;
	alias_direct = mesh;
	vtx_ptr = mesh->numverts;
	idx_ptr = mesh->numindices;
	return 1;
}

int
R_BufferAliasPositions(const float *positions)
{
	if (!alias_direct || !OFGL_AliasPositionsEnabled()) return 0;
	alias_positions = positions;
	return positions != NULL;
}

int
R_DrawNativeAlias(const ofgl_native_draw_t *draw)
{
	/* The native path only accepts a standalone ordinary opaque alias draw.
	 * Existing buffered draws must keep their original state and order. */
	if (!OFGL_NativeAvailable() || gl_buf.type != buf_alias ||
		world_count || vtx_ptr || idx_ptr || alias_direct) return 0;
	R_EnableMultitexture(false);
	R_Bind(gl_buf.texture[0]);
	glShadeModel(GL_SMOOTH);
	R_TexEnv(GL_MODULATE);
	int accepted = OFGL_DrawNativeAlias(draw);
	R_TexEnv(GL_REPLACE);
	glShadeModel(GL_FLAT);
	return accepted;
}

void
R_BufferAliasIndices(const of_alias_mesh_t *mesh)
{
	if (alias_direct || world_count || vtx_ptr + mesh->numverts >= MAX_VERTICES ||
		idx_ptr + mesh->numindices >= MAX_INDICES)
		R_ApplyGLBuffer();
	const unsigned short *indices = (const unsigned short *)(mesh->vertices + mesh->numverts);
	for (int i = 0; i < mesh->numindices; i++)
		gl_buf.idx[idx_ptr++] = vtx_ptr + indices[i];
	vtx_ptr += mesh->numverts;
}
#endif

extern void R_SetPerspective(GLdouble fovy);

void
R_ResetGLBuffer(void)
{
	GLBUFFER_RESET
#ifdef YQ2_OPENFPGA
	world_count = 0;
	alias_direct = NULL;
	alias_positions = NULL;
#endif
}

/*
 * Draws what's stored in the buffer and clears it up
 */
void
R_ApplyGLBuffer(void)
{
	// Properties of batched draws here
	GLint vtx_size;
	qboolean texture, mtex, alpha, color, alias, texenv_set;

	if ((vtx_ptr == 0 || idx_ptr == 0)
#ifdef YQ2_OPENFPGA
		&& world_count == 0
#endif
	)
	{
		return;
	}

	// defaults for drawing (mostly buf_singletex features)
	vtx_size = 3;
	texture = true;
	mtex = alpha = color = alias = texenv_set = false;

	// choosing features by type
	switch (gl_buf.type)
	{
		case buf_2d:
			vtx_size = 2;
			break;
		case buf_mtex:
			mtex = true;
			break;
		case buf_alpha:
			alpha = true;
			break;
		case buf_alias:
			alias = color = true;
			break;
		case buf_flash:
			color = true;
			/* fall through */
		case buf_shadow:
			texture = false;
			break;
		default:
			break;
	}

	R_EnableMultitexture(mtex);

	if (alias)
	{
		if (gl_buf.flags & RF_DEPTHHACK)
		{
			// hack the depth range to prevent view model from poking into walls
			glDepthRange(gldepthmin, gldepthmin + 0.3 * (gldepthmax - gldepthmin));
		}

		if (gl_buf.flags & RF_WEAPONMODEL)
		{
			glMatrixMode(GL_PROJECTION);
			glPushMatrix();
			glLoadIdentity();

			if (gl_lefthand->value == 1.0f)
			{
				glScalef(-1, 1, 1);
			}

			R_SetPerspective( (r_gunfov->value < 0) ? r_newrefdef.fov_y : r_gunfov->value );

			glMatrixMode(GL_MODELVIEW);

			if (gl_lefthand->value == 1.0f)
			{
				glCullFace(GL_BACK);
			}
		}

		glShadeModel(GL_SMOOTH);
		R_TexEnv(GL_MODULATE);

		if (gl_buf.flags & RF_TRANSLUCENT)
		{
			glEnable(GL_BLEND);
		}

		if (gl_buf.flags & (RF_SHELL_RED | RF_SHELL_GREEN |
			RF_SHELL_BLUE | RF_SHELL_DOUBLE | RF_SHELL_HALF_DAM))
		{
			texture = false;
			glDisable(GL_TEXTURE_2D);
		}
	}

	if (alpha)
	{
		// the textures are prescaled up for a better
		// lighting range, so scale it back down
		glColor4f(gl_state.inverse_intensity, gl_state.inverse_intensity,
				  gl_state.inverse_intensity, gl_buf.alpha);

	}
	else if (gl_buf.flags & SURF_DRAWTURB)
	{
		texenv_set = true;

		// This is a hack ontop of a hack. Warping surfaces like those generated
		// by R_EmitWaterPolys() don't have a lightmap. Original Quake II therefore
		// negated the global intensity on those surfaces, because otherwise they
		// would show up much too bright. When we implemented overbright bits this
		// hack modified the global GL state in an incompatible way. So implement
		// a new hack, based on overbright bits... Depending on the value set to
		// gl1_overbrightbits the result is different:

		//  0: Old behaviour.
		//  1: No overbright bits on the global scene but correct lighting on
		//     warping surfaces.
		//  2,4: Overbright bits on the global scene but not on warping surfaces.
		//       They oversaturate otherwise.

		if (gl1_overbrightbits->value)
		{
			R_TexEnv(GL_COMBINE);
			glTexEnvi(GL_TEXTURE_ENV, GL_RGB_SCALE, 1);
		}
		else
		{
			R_TexEnv(GL_MODULATE);
			glColor4f(gl_state.inverse_intensity, gl_state.inverse_intensity,
					  gl_state.inverse_intensity, 1.0f);
		}
	}

	glEnableClientState( GL_VERTEX_ARRAY );
	glVertexPointer (vtx_size, GL_FLOAT, 0, gl_buf.vtx);

	if (texture)
	{
		if (mtex)
		{
			// TMU 1: Lightmap texture
			int lmtexture = gl_state.lightmap_textures + gl_buf.texture[1];
			if (gl_config.lightmapcopies)
			{
				// Bind appropiate lightmap copy for this frame
				lmtexture += MAX_LIGHTMAPS * cur_lm_copy;
			}
			R_MBind(GL_TEXTURE1, lmtexture);

			if (gl1_overbrightbits->value)
			{
				R_TexEnv(GL_COMBINE);
				glTexEnvi(GL_TEXTURE_ENV, GL_RGB_SCALE, gl1_overbrightbits->value);
			}

			glEnableClientState(GL_TEXTURE_COORD_ARRAY);
			glTexCoordPointer(2, GL_FLOAT, 0, gl_buf.tex[1]);

			// TMU 0: Color texture
			R_MBind(GL_TEXTURE0, gl_buf.texture[0]);
		}
		else
		{
			R_Bind(gl_buf.texture[0]);
		}

		glEnableClientState(GL_TEXTURE_COORD_ARRAY);
#ifdef YQ2_OPENFPGA
		if (alias_direct)
			glTexCoordPointer(2, GL_FLOAT, sizeof(alias_direct->vertices[0]),
				&alias_direct->vertices[0].s);
		else
#endif
		glTexCoordPointer(2, GL_FLOAT, 0, gl_buf.tex[0]);
	}

	if (color)
	{
		glEnableClientState(GL_COLOR_ARRAY);
		glColorPointer(4, GL_UNSIGNED_BYTE, 0, gl_buf.clr);
	}

	// All set, we can finally draw
#ifdef YQ2_OPENFPGA
	/* These buffer types are populated only by BSP world/brush surfaces,
	 * including flowing, translucent and uncached oversized polygons. */
	OFGL_SetWorldDraw(gl_buf.type == buf_singletex ||
		gl_buf.type == buf_mtex || gl_buf.type == buf_alpha);
	if (world_count)
	{
		OFGL_DrawWorldPolys(world_polys, world_count);
		world_count = 0;
	}
	else if (alias_direct && alias_positions)
	{
		OFGL_DrawAliasElements(&alias_direct->vertices[0].xyz,
			sizeof(alias_direct->vertices[0]), alias_positions,
			(const unsigned short *)(alias_direct->vertices + alias_direct->numverts), idx_ptr);
	}
	else
#endif
	glDrawElements(GL_TRIANGLES, idx_ptr, GL_UNSIGNED_SHORT,
#ifdef YQ2_OPENFPGA
		alias_direct ? (const unsigned short *)(alias_direct->vertices + alias_direct->numverts) :
#endif
		gl_buf.idx);
#ifdef YQ2_OPENFPGA
	OFGL_SetWorldDraw(0);
#endif
	// ... and now, turn back everything as it was

	if (color)
	{
		glDisableClientState(GL_COLOR_ARRAY);
	}

	if (texture)
	{
		glDisableClientState( GL_TEXTURE_COORD_ARRAY );
	}

	glDisableClientState( GL_VERTEX_ARRAY );

	if (texenv_set)
	{
		R_TexEnv(GL_REPLACE);
	}

	if (alias)
	{
		if (gl_buf.flags & (RF_SHELL_RED | RF_SHELL_GREEN |
			RF_SHELL_BLUE | RF_SHELL_DOUBLE | RF_SHELL_HALF_DAM))
		{
			glEnable(GL_TEXTURE_2D);
		}

		if (gl_buf.flags & RF_TRANSLUCENT)
		{
			glDisable(GL_BLEND);
		}

		R_TexEnv(GL_REPLACE);
		glShadeModel(GL_FLAT);

		if (gl_buf.flags & RF_WEAPONMODEL)
		{
			glMatrixMode(GL_PROJECTION);
			glPopMatrix();
			glMatrixMode(GL_MODELVIEW);
			if (gl_lefthand->value == 1.0F)
			{
				glCullFace(GL_FRONT);
			}
		}

		if (gl_buf.flags & RF_DEPTHHACK)
		{
			glDepthRange(gldepthmin, gldepthmax);
		}
	}

	GLBUFFER_RESET
#ifdef YQ2_OPENFPGA
	alias_direct = NULL;
	alias_positions = NULL;
#endif
}

/*
 * Sets current state of the buffer. Any change means "draw its contents now".
 */
void
R_UpdateGLBuffer(buffered_draw_t type, int colortex, int lighttex, int flags, float alpha)
{
	if ( gl_buf.type != type || gl_buf.texture[0] != colortex ||
		(gl_config.multitexture && type == buf_mtex && gl_buf.texture[1] != lighttex) ||
		((type == buf_singletex || type == buf_alias) && gl_buf.flags != flags) ||
		(type == buf_alpha && gl_buf.alpha != alpha))
	{
		R_ApplyGLBuffer();

		gl_buf.type = type;
		gl_buf.texture[0] = colortex;
		gl_buf.texture[1] = lighttex;
		gl_buf.flags = flags;
		gl_buf.alpha = alpha;
	}
}

/*
 * Stores a 2D drawing in the buffer.
 * ul = up-left corner, dr = down-right corner
 * v = vertex, t = texture, x/y = coordinates
 */
void
R_Buffer2DQuad(GLfloat ul_vx, GLfloat ul_vy, GLfloat dr_vx, GLfloat dr_vy,
	GLfloat ul_tx, GLfloat ul_ty, GLfloat dr_tx, GLfloat dr_ty)
{
	static const GLushort idx_max = MAX_INDICES - 7;
	static const GLushort vtx_max = MAX_VERTICES - 5;

#ifdef YQ2_OPENFPGA
	if (alias_direct) R_ApplyGLBuffer();
#endif
	if (idx_ptr > idx_max || vtx_ptr > vtx_max)
	{
		R_ApplyGLBuffer();
	}

	// "Quad" = 2-triangle GL_TRIANGLE_FAN
	gl_buf.idx[idx_ptr]   = vtx_ptr;
	gl_buf.idx[idx_ptr+1] = vtx_ptr+1;
	gl_buf.idx[idx_ptr+2] = vtx_ptr+2;
	gl_buf.idx[idx_ptr+3] = vtx_ptr;
	gl_buf.idx[idx_ptr+4] = vtx_ptr+2;
	gl_buf.idx[idx_ptr+5] = vtx_ptr+3;
	idx_ptr += 6;

	// up left corner coords
	gl_buf.vtx[gl_buf.vt]   = ul_vx;
	gl_buf.vtx[gl_buf.vt+1] = ul_vy;
	// up right
	gl_buf.vtx[gl_buf.vt+2] = dr_vx;
	gl_buf.vtx[gl_buf.vt+3] = ul_vy;
	// down right
	gl_buf.vtx[gl_buf.vt+4] = dr_vx;
	gl_buf.vtx[gl_buf.vt+5] = dr_vy;
	// and finally, down left
	gl_buf.vtx[gl_buf.vt+6] = ul_vx;
	gl_buf.vtx[gl_buf.vt+7] = dr_vy;

	gl_buf.tex[0][gl_buf.tx]   = ul_tx;
	gl_buf.tex[0][gl_buf.tx+1] = ul_ty;
	gl_buf.tex[0][gl_buf.tx+2] = dr_tx;
	gl_buf.tex[0][gl_buf.tx+3] = ul_ty;
	gl_buf.tex[0][gl_buf.tx+4] = dr_tx;
	gl_buf.tex[0][gl_buf.tx+5] = dr_ty;
	gl_buf.tex[0][gl_buf.tx+6] = ul_tx;
	gl_buf.tex[0][gl_buf.tx+7] = dr_ty;

	vtx_ptr += 4;
	gl_buf.vt += 8;
	gl_buf.tx += 8;
}

/*
 * Set up indices with the proper shape for the next buffered vertices.
 * After calling this, GLBUFFER_VERTEX() must be called 'vertices_num' times.
 */
void
R_SetBufferIndices(GLenum primitive, GLuint vertices_num)
{
	int i;
#ifdef YQ2_OPENFPGA
	if (world_count || alias_direct)
		R_ApplyGLBuffer();
#endif

	if ( vtx_ptr + vertices_num >= MAX_VERTICES ||
		idx_ptr + ( (vertices_num - 2) * 3 ) >= MAX_INDICES )
	{
		R_ApplyGLBuffer();
	}

	switch (primitive)
	{
		case GL_TRIANGLE_FAN:
			for (i = 0; i < vertices_num-2; i++)
			{
				gl_buf.idx[idx_ptr]   = vtx_ptr;
				gl_buf.idx[idx_ptr+1] = vtx_ptr+i+1;
				gl_buf.idx[idx_ptr+2] = vtx_ptr+i+2;
				idx_ptr += 3;
			}
			break;
		case GL_TRIANGLE_STRIP:
			for (i = 0; i < vertices_num-2; i++)
			{
				if (i % 2 == 0)
				{
					gl_buf.idx[idx_ptr]   = vtx_ptr+i;
					gl_buf.idx[idx_ptr+1] = vtx_ptr+i+1;
					gl_buf.idx[idx_ptr+2] = vtx_ptr+i+2;
				}
				else	// backwards order
				{
					gl_buf.idx[idx_ptr]   = vtx_ptr+i+2;
					gl_buf.idx[idx_ptr+1] = vtx_ptr+i+1;
					gl_buf.idx[idx_ptr+2] = vtx_ptr+i;
				}
				idx_ptr += 3;
			}
			break;
		default:
			Com_DPrintf("%s: no such primitive %d\n", __func__, primitive);
			return;
	}

	// GLBUFFER_VERTEX() must be called as many times as vertices_num
	vtx_ptr += vertices_num;
}
