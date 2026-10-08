/* Only mesh_shadow_capture_vertex writes this persistent shader state. The
 * matrix and normal coefficients have the same invalidation epochs as the
 * original renderer; dynamic colors, UV scale, geometry and fog remain LOAD
 * state. Reserved words stay at their static zero initialization. */
static uint32_t mesh_shader_matrix_epoch,mesh_shader_aspect_bits;
static uint32_t mesh_shader_light_epoch;
static unsigned mesh_shader_matrix_valid,mesh_shader_light_valid;
static unsigned mesh_shader_lighting,mesh_shader_lights;
static int mesh_shadow_update_shader(uint32_t *state,float aspect) {
    uint32_t aspect_bits;memcpy(&aspect_bits,&aspect,4);
    if(!mesh_shader_matrix_valid || mesh_shader_matrix_epoch!=geometry_mvp_epoch
       || mesh_shader_aspect_bits!=aspect_bits) {
        /* Invalidate before partial writes: a failed conversion must never
         * make a later hit reuse a partially updated matrix. */
        mesh_shader_matrix_valid=0;
        for(unsigned row=0;row<4;row++)for(unsigned k=0;k<4;k++) {
            float original=RSPV_TO_FLOAT(rsp.MP_matrix[k][row]);
            memcpy(&state[MESH_STATE_FLOAT_MATRIX+row*4+k],&original,4);
            float f=original*(row==0?aspect:1.0f);
            if(!(fabsf(f)<(k==3?8000000.0f:127.0f)))return 4;
            state[row*4+k]=(int32_t)lrintf(f*(k==3?256.0f:16777216.0f));
        }
        state[56]=aspect_bits;
        mesh_shader_matrix_epoch=geometry_mvp_epoch;
        mesh_shader_aspect_bits=aspect_bits;mesh_shader_matrix_valid=1;
    }
    unsigned lighting=(rsp.geometry_mode&G_LIGHTING)!=0;
    unsigned lights=rsp.current_num_lights?rsp.current_num_lights-1:0;
    if((rsp.geometry_mode&G_TEXTURE_GEN)||(lighting&&lights>1))return 5;
    if(!mesh_shader_light_valid || mesh_shader_light_epoch!=lighting_epoch
       || mesh_shader_lighting!=lighting || mesh_shader_lights!=lights) {
        mesh_shader_light_valid=0;
        for(unsigned k=0;k<3;k++) {
            float c=lighting&&lights?RSPV_TO_FLOAT(rsp.current_lights_coeffs[0][k]):0.0f;
            if(!(fabsf(c)<=1.000001f))return 5;
            state[16+k]=(int32_t)lrintf(c*(1073741824.0f/127.0f));
        }
        mesh_shader_light_epoch=lighting_epoch;mesh_shader_lighting=lighting;
        mesh_shader_lights=lights;mesh_shader_light_valid=1;
    }
    /* Colors can change while lighting is disabled, without advancing the
     * normal-coefficient epoch. They are deliberately captured every LOAD. */
    state[19]=state[20]=0;
    for(unsigned k=0;k<3;k++) {
        state[19]|=(uint32_t)rsp.current_lights[0].col[k]<<(8*k);
        state[20]|=(uint32_t)rsp.current_lights[lights].col[k]<<(8*k);
    }
    return 0;
}
