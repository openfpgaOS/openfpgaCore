// Append under INCLUDE_GPU_ASYNC_MESH to gpu_core and phase10_load_alias.
// Directions below are phase10_load_alias directions; GPU directions invert.
input wire mesh_ctx_valid,
output wire mesh_ctx_ready,
input wire [4:0] mesh_ctx_index,
input wire [31:0] mesh_ctx_data,
input wire mesh_job_valid,
output wire mesh_job_ready,
input wire [31:0] mesh_job_seq, mesh_job_vertex, mesh_job_vtx_w0, mesh_job_commands,
input wire [31:0] mesh_job_control, mesh_job_flags, mesh_job_uofs, mesh_job_vofs,
input wire [31:0] mesh_job_cpu_vertex, mesh_job_cpu_commands, mesh_job_alpha,
output wire mesh_word_valid,
output wire [31:0] mesh_word_data,
input wire mesh_word_ready,
output wire mesh_job_done, mesh_job_fault,
input wire mesh_consumer_reset, mesh_bridge_fault
