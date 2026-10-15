/* Reuse the real GL renderer; isolate native immutable asset upload/lifetime. */
#define main renderer_existing_main
#include "quake2_renderer_test.c"
#undef main

static uint32_t epoch = 23, memory[256], next_address = 0x8000;
static unsigned reserves, uploads, allow_reserve = 1;
uint32_t OFQ2_NativeGeneration(void) { return epoch; }
int OFQ2_NativeReserve(uint32_t bytes, uint32_t *address)
{
	++reserves;
	if (!allow_reserve) return 0;
	assert(bytes == (6 + 11) * 4);
	*address = next_address;
	return 1;
}
void OFQ2_NativeUpload(uint32_t address, const uint32_t *words, uint32_t count)
{
	assert(address >= next_address && (address - next_address) / 4 + count <= 256);
	memcpy(memory + (address - next_address) / 4, words, count * 4);
	++uploads;
}

int main(void)
{
	uint32_t frames[6] = {0x01433221, 0x02554411, 0x03552266,
		0x04654321, 0x05fedcba, 0x06123456};
	uint32_t blocks[11] = {3 | (1 << 8), 0,0,0, 1,0,0, 2,0,0, 0 | (1 << 5) | (2 << 10)};
	float uv[6] = {.25f,.5f, .75f,1, -.5f,1.25f};
	for (unsigned v = 0; v < 3; ++v) memcpy(blocks + 2 + v * 3, uv + v * 2, 8);
	ofgl_native_mesh_t mesh = {.blocks = blocks, .block_words = 11, .block_count = 1,
		.source_vertices = 3, .frame_count = 2, .frames = (uint8_t *)frames, .frame_stride = 12};
	assert(ofgl_native_upload(&mesh, 64, 32));
	assert(reserves == 1 && uploads == 3 && mesh.gpu_generation == epoch);
	assert(mesh.gpu_frames == next_address && mesh.gpu_blocks == next_address + 24 && mesh.gpu_end == next_address + 68);
	assert(!memcmp(memory, frames, sizeof(frames)));
	for (unsigned v = 0; v < 3; ++v) {
		assert(memory[6 + 1 + v * 3] == v);
		assert((int32_t)memory[6 + 2 + v * 3] == (int32_t)lrintf(uv[v * 2] * 64 * 65536));
		assert((int32_t)memory[6 + 3 + v * 3] == (int32_t)lrintf(uv[v * 2 + 1] * 32 * 65536));
	}
	for (unsigned n = 0; n < 100; ++n) {
		assert(ofgl_native_upload(&mesh, 64, 32));
		assert(!ofgl_native_upload(&mesh, 128, 64));
		assert(!ofgl_native_upload(&mesh, 64, 64));
	}
	assert(reserves == 1 && uploads == 3); /* no alternation upload/fence */
	++epoch; next_address = 0x9000; allow_reserve = 0;
	assert(!ofgl_native_upload(&mesh, 128, 64) && mesh.gpu_generation != epoch);
	assert(reserves == 2 && uploads == 3);
	allow_reserve = 1;
	assert(ofgl_native_upload(&mesh, 128, 64));
	assert(reserves == 3 && uploads == 6 && mesh.gpu_generation == epoch && mesh.gpu_width == 128 && mesh.gpu_height == 64);
	puts("PASS: immutable native frame/UV upload, repeated/alternating skin dimensions, zero resident waits, generation refresh and allocation fallback");
}
