/* Exercise the real sound startup before any renderer initialization. */
#include "of.h"
#include <assert.h>
#include <sys/mman.h>

static unsigned audio_inits, mixer_inits;
static void test_audio_init(void) { ++audio_inits; }
static void test_mixer_init(int voices, int rate)
{
	assert(voices == OF_MIXER_MAX_VOICES);
	assert(rate == OF_MIXER_OUTPUT_RATE);
	++mixer_inits;
}
#define of_audio_init test_audio_init
#define of_mixer_init test_mixer_init
#include "../../src/quake2/openfpga/snd_of.c"
#undef of_audio_init
#undef of_mixer_init

sound_t sound;
int s_numchannels;
cvar_t *s_volume;
static struct of_capabilities test_caps;
static const struct of_capabilities *current_caps = &test_caps;
const struct of_capabilities *of_get_caps(void) { return current_caps; }
void Com_Printf(const char *format, ...) { (void)format; }

static void check_start(uint32_t features, uint32_t gpu_base, int expected)
{
	test_caps.hw_features = features;
	test_caps.gpu_base = gpu_base;
	audio_inits = mixer_inits = 0;
	snd_inited = false;
	assert(SDL_BackendInit() == expected);
	assert(snd_inited == expected);
	assert(audio_inits == (unsigned)expected);
	assert(mixer_inits == (unsigned)expected);
	if (expected)
	{
		assert(sound.speed == 22050 && sound.channels == 2);
		assert(sound.buffer == NULL && s_numchannels == MAX_CHANNELS);
		for (unsigned i = 0; i < MAX_CHANNELS; ++i)
			assert(snd_handle[i] == OF_MIXER_HANDLE_INVALID);
	}
}

int main(void)
{
	/* caps carries 32-bit target addresses. A low-address mapping lets
	 * this host fixture exercise the real volatile MMIO load unchanged. */
	void *registers = mmap((void *)(uintptr_t)0x10000000u, 4096,
		PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	assert(registers != MAP_FAILED && (uintptr_t)registers <= UINT32_MAX - 4096);
	volatile uint32_t *status = (volatile uint32_t *)((char *)registers + 0x14);
	uint32_t gpu_base = (uint32_t)(uintptr_t)registers;
	*status = 1u << 12;
	check_start(OF_HW_MIXER_HW, gpu_base, true);
	/* Hardware mixer present must not even touch the GPU MMIO window. */
	check_start(OF_HW_MIXER_HW, 1, true);
	check_start(0, gpu_base, false);
	*status = 0;
	check_start(0, gpu_base, true); /* previous cores retain their behavior */
	*status = 3u << 10;
	check_start(0, gpu_base, true); /* older geometry worker is not native */
	check_start(0, 0, true);
	current_caps = NULL;
	check_start(0, 0, true);
	assert(munmap(registers, 4096) == 0);
	puts("PASS: native mixer-less startup stays silent; hardware mixer startup unchanged");
	return 0;
}
