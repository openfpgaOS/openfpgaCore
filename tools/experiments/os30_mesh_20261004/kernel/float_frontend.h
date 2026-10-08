/* Private cost probe for an exact SM64 float companion front end.
 * No transport, production integration or prepared-plane implementation. */
#ifndef MESH_FLOAT_FRONTEND_H
#define MESH_FLOAT_FRONTEND_H
#include <stdint.h>
#include <stddef.h>
struct MFSource { float position[3]; int16_t uv[2]; uint8_t color[4]; };
struct MFVertex {
    float x,y,z,w,wi,u,v;
    uint8_t color[4],clip;
    uint32_t depth,exact_depth;
};
struct MFShader {
    float matrix[4][4],light_coeff[2][3],lookat[2][3],aspect;
    uint8_t light[3][3];
    uint32_t lights,geometry_mode,fog_enabled;
    uint16_t scale_s,scale_t;
    int32_t fog_mul,fog_offset;
    uint32_t fixed;
    int32_t fixed_matrix[12]; /* x,y,w Q8.24 coefficients/Q24.8 translation */
};
struct MFMaterial {
    float cx,cy,hw,hh;
    int32_t uls,ult;
    uint32_t textured,bias,constant_rgb,rgb,clip_x,clip_y;
};
void mf_transform(const struct MFShader *,const struct MFSource *,unsigned,struct MFVertex *);
int mf_cull(const struct MFVertex [3],uint32_t geometry_mode);
unsigned mf_clip(const struct MFVertex [3],struct MFVertex [12]);
/* cached!=0 preserves coarse GPU reciprocal + normalize. Otherwise preserves
 * the original continuous per-triangle perspective scale. Supported material:
 * ordinary truecolor C=shade/constant, D=0, no decal or LOD or second pass. */
void mf_project(const struct MFVertex [3],const struct MFMaterial *,unsigned cached,uint32_t [24]);
#endif
