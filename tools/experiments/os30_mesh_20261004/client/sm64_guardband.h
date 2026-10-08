/* Exact original geometry_prepare LOAD-time eligibility, without vertex/face
 * work or geometry-context allocation. Integral immutable bounds are cached
 * when the mesh raw input is first decoded. Shared original float plane math
 * preserves the production renderer's guardband decision and precision path. */
static int mesh_guardband_accepted(const struct MeshVertexEntry *entry,
        unsigned count,unsigned first,float aspect) {
    if(gCurrLevelNum<=LEVEL_UNKNOWN_3 || first>=32 || count>32-first || count<8
       || !(rsp.geometry_mode&G_ZBUFFER) || (rsp.geometry_mode&G_TEXTURE_GEN)
       || (rsp.MP_matrix[0][3]==0 && rsp.MP_matrix[1][3]==0
           && rsp.MP_matrix[2][3]==0))return 0;
    float box[6];
    for(unsigned k=0;k<6;k++)box[k]=entry->bounds[k];
    if(!((box[0]!=box[1] && rsp.MP_matrix[0][3]!=0)
       ||(box[2]!=box[3] && rsp.MP_matrix[1][3]!=0)
       ||(box[4]!=box[5] && rsp.MP_matrix[2][3]!=0)))return 0;
    geometry_update_planes(aspect);
    const unsigned first_planes[4]={0,1,6,7};
    for(unsigned i=0;i<4;i++) {
        const struct GeometryPlane *p=&geometry_planes[first_planes[i]];
        float minimum=box[p->corner[0]]*p->c[0]+box[p->corner[1]]*p->c[1]
                     +box[p->corner[2]]*p->c[2]+p->c[3];
        if(!(minimum>0.0f))return 0;
    }
    for(unsigned i=0;i<4;i++) {
        const struct GeometryPlane *p=&geometry_frustum_planes[i];
        float minimum=box[p->corner[0]]*p->c[0]+box[p->corner[1]]*p->c[1]
                     +box[p->corner[2]]*p->c[2]+p->c[3];
        if(minimum>256.0f)continue;
        float maximum=box[p->corner[0]^1]*p->c[0]+box[p->corner[1]^1]*p->c[1]
                     +box[p->corner[2]^1]*p->c[2]+p->c[3];
        if(maximum < -2.0f)return 0;
        p=&geometry_planes[i+2];
        minimum=box[p->corner[0]]*p->c[0]+box[p->corner[1]]*p->c[1]
               +box[p->corner[2]]*p->c[2]+p->c[3];
        if(!(minimum>0.0f))return 0;
    }
    return 1;
}
