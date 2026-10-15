/*
 * openfpgaOS hardware-mixer sound backend for yquake2.
 *
 * Replaces the SDL software-mixing backend (client/sound/sdl.c)
 * wholesale: it exports the same SDL_* symbols sound.c dispatches to
 * under SS_SDL, but plays every channel on a hardware mixer voice
 * (32 voices, 48 kHz output, per-voice 16.16 resampling and stereo
 * volume). There is no per-frame CPU mixing at all:
 *
 *   - one-shot channels  -> one mixer voice each, started when the
 *     engine (re)issues the channel, stereo volume refreshed from the
 *     engine's own spatialization every frame, reaped when the
 *     hardware reports the voice ended;
 *   - entity loop sounds -> persistent looping voices keyed by sfx,
 *     bypassing the channel array entirely (the SDL backend recreates
 *     autosound channels every frame; a voice keeps phase naturally);
 *   - cinematic / OGG raw samples -> the kernel's resampling stream
 *     voice via of_audio (the same path the SDL shim used);
 *   - master volume -> the mixer's SFX volume group.
 *
 * Samples are cached at their NATIVE rate (the hardware resamples per
 * voice), so S_LoadSound's resample inflation disappears too.
 */

#include "../src/client/header/client.h"
#include "../src/client/sound/header/local.h"
#include "../src/client/sound/header/vorbis.h"

#include "of.h"

#define OF_SND_FULLVOLUME 80
#define OF_SND_LOOPATTENUATE 0.003f

/* mixer voice priority for SFX (loops slightly lower so a burst of
 * one-shots steals from ambience, not from other effects) */
#define OF_SND_PRIO_ONESHOT 2
#define OF_SND_PRIO_LOOP 1

/* per-channel voice tracking: (sfx, end) identifies one issue of a
 * channel slot — sound.c stamps end = paintedtime + length, which is
 * unique per issue */
static of_mixer_handle_t snd_handle[MAX_CHANNELS];
static sfx_t *snd_shadow_sfx[MAX_CHANNELS];
static int snd_shadow_end[MAX_CHANNELS];

/* persistent looping voices for entity ambient sounds */
#define OF_SND_MAX_LOOPS 16
typedef struct {
	sfx_t *sfx;
	of_mixer_handle_t handle;
	unsigned stamp;
} of_snd_loop_t;
static of_snd_loop_t snd_loops[OF_SND_MAX_LOOPS];
static unsigned snd_loop_stamp;

static int soundtime;
static int beginofs;
static int snd_raw_rate;
static qboolean snd_inited;

/*
 * ---------------------------------------------------------------
 * helpers
 * ---------------------------------------------------------------
 */

static void
OF_SND_VoiceVolume(of_mixer_handle_t h, int left, int right)
{
	if (left < 0) left = 0;
	if (left > 255) left = 255;
	if (right < 0) right = 0;
	if (right > 255) right = 255;
	of_mixer_set_vol_lr_h(h, left, right);
}

static void
OF_SND_StopChannelVoice(int i)
{
	if (snd_handle[i] != OF_MIXER_HANDLE_INVALID)
	{
		of_mixer_stop_h(snd_handle[i]);
		snd_handle[i] = OF_MIXER_HANDLE_INVALID;
	}
	snd_shadow_sfx[i] = NULL;
	snd_shadow_end[i] = 0;
}

static void
OF_SND_StopLoops(void)
{
	int i;

	for (i = 0; i < OF_SND_MAX_LOOPS; i++)
	{
		if (snd_loops[i].sfx)
		{
			of_mixer_stop_h(snd_loops[i].handle);
			snd_loops[i].sfx = NULL;
			snd_loops[i].handle = OF_MIXER_HANDLE_INVALID;
		}
	}
}

/* identical math to the SDL backend's spatializer */
static void
OF_SND_SpatializeOrigin(vec3_t origin, float master_vol, float dist_mult,
		int *left_vol, int *right_vol)
{
	vec_t dot;
	vec_t dist;
	vec_t lscale, rscale, scale;
	vec3_t source_vec;

	if (cls.state != ca_active)
	{
		*left_vol = *right_vol = 255;
		return;
	}

	VectorSubtract(origin, listener_origin, source_vec);

	dist = VectorNormalize(source_vec);
	dist -= OF_SND_FULLVOLUME;

	if (dist < 0)
	{
		dist = 0;
	}

	dist *= dist_mult;
	dot = DotProduct(listener_right, source_vec);

	if (!dist_mult)
	{
		rscale = 1.0f;
		lscale = 1.0f;
	}
	else
	{
		rscale = 0.5f * (1.0f + dot);
		lscale = 0.5f * (1.0f - dot);
	}

	scale = (1.0f - dist) * rscale;
	*right_vol = (int)(master_vol * scale);

	if (*right_vol < 0)
	{
		*right_vol = 0;
	}

	scale = (1.0f - dist) * lscale;
	*left_vol = (int)(master_vol * scale);

	if (*left_vol < 0)
	{
		*left_vol = 0;
	}
}

void
SDL_Spatialize(channel_t *ch)
{
	vec3_t origin;

	/* anything from the view entity is always full volume */
	if (ch->entnum == cl.playernum + 1)
	{
		ch->leftvol = ch->master_vol;
		ch->rightvol = ch->master_vol;
		return;
	}

	if (ch->fixed_origin)
	{
		VectorCopy(ch->origin, origin);
	}
	else
	{
		GetEntitySoundOrigin(ch->entnum, listener_origin, origin);
	}

	OF_SND_SpatializeOrigin(origin, (float)ch->master_vol, ch->dist_mult,
		&ch->leftvol, &ch->rightvol);
}

/*
 * ---------------------------------------------------------------
 * sample cache — native rate, always signed 16-bit mono
 * ---------------------------------------------------------------
 */

qboolean
SDL_Cache(sfx_t *sfx, wavinfo_t *info, byte *data, short volume,
		int begin_length, int end_length, int attack_length, int fade_length)
{
	sfxcache_t *sc;
	int samples = info->samples;
	int i;
	int16_t *out;

	if (samples <= 0 || info->channels < 1)
	{
		return false;
	}

	/* stereo source (rare) is downmixed; the mixer voices are mono */
	sc = sfx->cache = Z_Malloc(sizeof(sfxcache_t) +
		(size_t)samples * sizeof(int16_t) - sizeof(sc->data));

	if (!sc)
	{
		return false;
	}

	sc->length = samples;
	sc->loopstart = info->loopstart;
	sc->speed = info->rate;
	sc->width = 2;
	sc->stereo = 0;
	sc->volume = volume;
	sc->begin = begin_length * 1000 / info->rate;
	sc->end = end_length * 1000 / info->rate;
	sc->attack = attack_length * 1000 / info->rate;
	sc->fade = fade_length * 1000 / info->rate;

	out = (int16_t *)sc->data;

	if (info->width == 2)
	{
		const int16_t *in = (const int16_t *)data;

		if (info->channels == 2)
		{
			for (i = 0; i < samples; i++)
			{
				out[i] = (int16_t)(((int)LittleShort(in[i * 2]) +
					(int)LittleShort(in[i * 2 + 1])) / 2);
			}
		}
		else
		{
			for (i = 0; i < samples; i++)
			{
				out[i] = LittleShort(in[i]);
			}
		}
	}
	else
	{
		/* WAV 8-bit is unsigned */
		const uint8_t *in = (const uint8_t *)data;

		if (info->channels == 2)
		{
			for (i = 0; i < samples; i++)
			{
				out[i] = (int16_t)((((int)in[i * 2] +
					(int)in[i * 2 + 1]) / 2 - 128) << 8);
			}
		}
		else
		{
			for (i = 0; i < samples; i++)
			{
				out[i] = (int16_t)(((int)in[i] - 128) << 8);
			}
		}
	}

	return true;
}

/*
 * ---------------------------------------------------------------
 * one-shot channels
 * ---------------------------------------------------------------
 */

static void
OF_SND_StartChannelVoice(int i, channel_t *ch)
{
	sfxcache_t *sc = ch->sfx ? ch->sfx->cache : NULL;
	of_mixer_handle_t h;

	OF_SND_StopChannelVoice(i);

	if (!sc || sc->length <= 0)
	{
		return;
	}

	h = of_mixer_alloc_for_group_h(OF_MIXER_GROUP_SFX,
		(const uint8_t *)sc->data, (uint32_t)sc->length,
		(uint32_t)sc->speed, OF_SND_PRIO_ONESHOT, 0);

	if (h == OF_MIXER_HANDLE_INVALID)
	{
		return;
	}

	if (sc->loopstart >= 0 && sc->loopstart < sc->length)
	{
		of_mixer_set_loop_h(h, sc->loopstart, sc->length);
	}

	OF_SND_VoiceVolume(h, ch->leftvol, ch->rightvol);

	snd_handle[i] = h;
	snd_shadow_sfx[i] = ch->sfx;
	snd_shadow_end[i] = ch->end;
}

/*
 * ---------------------------------------------------------------
 * entity loop sounds — persistent voices keyed by sfx
 * ---------------------------------------------------------------
 */

static void
OF_SND_UpdateLoopVoice(sfx_t *sfx, int left, int right)
{
	of_snd_loop_t *slot = NULL;
	sfxcache_t *sc = sfx->cache;
	int i;

	if (!sc || sc->length <= 0)
	{
		return;
	}

	for (i = 0; i < OF_SND_MAX_LOOPS; i++)
	{
		if (snd_loops[i].sfx == sfx)
		{
			slot = &snd_loops[i];
			break;
		}
		if (!slot && !snd_loops[i].sfx)
		{
			slot = &snd_loops[i];
		}
	}

	if (!slot)
	{
		return;
	}

	if (slot->sfx != sfx)
	{
		of_mixer_handle_t h = of_mixer_alloc_for_group_h(OF_MIXER_GROUP_SFX,
			(const uint8_t *)sc->data, (uint32_t)sc->length,
			(uint32_t)sc->speed, OF_SND_PRIO_LOOP, 0);

		if (h == OF_MIXER_HANDLE_INVALID)
		{
			return;
		}

		of_mixer_set_loop_h(h,
			(sc->loopstart >= 0 && sc->loopstart < sc->length) ?
				sc->loopstart : 0,
			sc->length);

		slot->sfx = sfx;
		slot->handle = h;
	}

	OF_SND_VoiceVolume(slot->handle, left, right);
	slot->stamp = snd_loop_stamp;
}

static void
OF_SND_AddLoopSounds(void)
{
	int i;
	int sounds[MAX_EDICTS];

	snd_loop_stamp++;

	if ((cls.state == ca_active) &&
		!(cl_paused->value && cl_audiopaused->value) &&
		cl.sound_prepped && s_ambient->value)
	{
		memset(&sounds, 0, sizeof(int) * MAX_EDICTS);
		S_BuildSoundList(sounds);

		for (i = 0; i < cl.frame.num_entities; i++)
		{
			int left, right, left_total, right_total;
			sfx_t *sfx;
			int num;
			entity_state_t *ent;
			vec3_t org;
			int j;

			if (!sounds[i])
			{
				continue;
			}

			sfx = cl.sound_precache[sounds[i]];

			if (!sfx || !sfx->cache)
			{
				continue;
			}

			num = (cl.frame.parse_entities + i) & (MAX_PARSE_ENTITIES - 1);
			ent = &cl_parse_entities[num];

			if (!GetBSPEntitySoundOrigin(ent->number, listener_origin, org))
			{
				VectorCopy(ent->origin, org);
			}

			OF_SND_SpatializeOrigin(org, 255.0f, OF_SND_LOOPATTENUATE,
				&left_total, &right_total);

			for (j = i + 1; j < cl.frame.num_entities; j++)
			{
				if (sounds[j] != sounds[i])
				{
					continue;
				}

				sounds[j] = 0;
				num = (cl.frame.parse_entities + j) & (MAX_PARSE_ENTITIES - 1);
				ent = &cl_parse_entities[num];

				if (!GetBSPEntitySoundOrigin(ent->number, listener_origin, org))
				{
					VectorCopy(ent->origin, org);
				}

				OF_SND_SpatializeOrigin(org, 255.0f, OF_SND_LOOPATTENUATE,
					&left, &right);

				left_total += left;
				right_total += right;
			}

			if ((left_total == 0) && (right_total == 0))
			{
				continue;
			}

			OF_SND_UpdateLoopVoice(sfx, left_total, right_total);
		}
	}

	/* silence loop voices whose entity went away (or ambience off) */
	for (i = 0; i < OF_SND_MAX_LOOPS; i++)
	{
		if (snd_loops[i].sfx && snd_loops[i].stamp != snd_loop_stamp)
		{
			of_mixer_stop_h(snd_loops[i].handle);
			snd_loops[i].sfx = NULL;
			snd_loops[i].handle = OF_MIXER_HANDLE_INVALID;
		}
	}
}

/*
 * ---------------------------------------------------------------
 * raw samples (cinematics, OGG music) — kernel stream voice
 * ---------------------------------------------------------------
 */

void
SDL_RawSamples(int samples, int rate, int width, int channels,
		const byte *data, float volume)
{
	static int16_t conv[2048];
	int frames_left = samples;

	if (samples <= 0 || !data || rate <= 0)
	{
		return;
	}

	if (rate != snd_raw_rate)
	{
		of_audio_stream_open(rate);
		snd_raw_rate = rate;
	}

	if (volume > 1.0f)
	{
		volume = 1.0f;
	}
	if (volume < 0.0f)
	{
		volume = 0.0f;
	}

	while (frames_left > 0)
	{
		int chunk = frames_left;
		int room = of_audio_free();
		int i;
		int base = samples - frames_left;

		if (chunk > 1024)
		{
			chunk = 1024;
		}
		if (chunk > room)
		{
			chunk = room;
		}
		if (chunk <= 0)
		{
			break;	/* FIFO full — drop the tail like the shim did */
		}

		for (i = 0; i < chunk; i++)
		{
			int s = base + i;
			int l, r;

			if (width == 2)
			{
				const int16_t *in = (const int16_t *)data;

				if (channels == 2)
				{
					l = LittleShort(in[s * 2]);
					r = LittleShort(in[s * 2 + 1]);
				}
				else
				{
					l = r = LittleShort(in[s]);
				}
			}
			else
			{
				const uint8_t *in = (const uint8_t *)data;

				if (channels == 2)
				{
					l = ((int)in[s * 2] - 128) << 8;
					r = ((int)in[s * 2 + 1] - 128) << 8;
				}
				else
				{
					l = r = ((int)in[s] - 128) << 8;
				}
			}

			conv[i * 2] = (int16_t)(l * volume);
			conv[i * 2 + 1] = (int16_t)(r * volume);
		}

		of_audio_write(conv, chunk);
		frames_left -= chunk;
	}

	s_rawend += samples;
}

/*
 * ---------------------------------------------------------------
 * frame update
 * ---------------------------------------------------------------
 */

void
SDL_Update(void)
{
	channel_t *ch;
	int i;

	if (!snd_inited)
	{
		return;
	}

	/* sample clock: paintedtime advances in real time at sound.speed */
	soundtime = (int)(((uint64_t)of_time_ms() *
		(uint32_t)sound.speed) / 1000u);
	paintedtime = soundtime;

	/* loading plaque: silence everything */
	if (cls.disable_screen)
	{
		SDL_ClearBuffer();
		return;
	}

	/* master volume -> hardware SFX group */
	if (s_volume->modified)
	{
		float v = s_volume->value;

		s_volume->modified = false;
		if (v < 0.0f) v = 0.0f;
		if (v > 1.0f) v = 1.0f;
		of_mixer_set_group_volume(OF_MIXER_GROUP_SFX, (int)(v * 255.0f));
	}

	/* issue pending playsounds whose start time has arrived (the SDL
	 * backend does this inside its paint loop) */
	while (s_pendingplays.next != &s_pendingplays &&
		s_pendingplays.next->begin <= paintedtime)
	{
		S_IssuePlaysound(s_pendingplays.next);
	}

	/* one-shot channels */
	ch = channels;

	for (i = 0; i < s_numchannels; i++, ch++)
	{
		if (!ch->sfx)
		{
			if (snd_handle[i] != OF_MIXER_HANDLE_INVALID)
			{
				OF_SND_StopChannelVoice(i);
			}
			continue;
		}

		if (ch->autosound)
		{
			/* never used by this backend; clear defensively */
			memset(ch, 0, sizeof(*ch));
			continue;
		}

		/* respatialize */
		SDL_Spatialize(ch);

		/* new issue on this slot? -> (re)start the voice */
		if (snd_shadow_sfx[i] != ch->sfx || snd_shadow_end[i] != ch->end)
		{
			OF_SND_StartChannelVoice(i, ch);
			continue;
		}

		if (snd_handle[i] == OF_MIXER_HANDLE_INVALID)
		{
			continue;
		}

		/* voice finished on its own? -> free the engine channel */
		if (!of_mixer_handle_active(snd_handle[i]))
		{
			snd_handle[i] = OF_MIXER_HANDLE_INVALID;
			snd_shadow_sfx[i] = NULL;
			snd_shadow_end[i] = 0;
			memset(ch, 0, sizeof(*ch));
			continue;
		}

		OF_SND_VoiceVolume(snd_handle[i], ch->leftvol, ch->rightvol);
	}

	/* entity ambient loops on persistent voices */
	OF_SND_AddLoopSounds();

	if (s_show->value)
	{
		int total = 0;

		ch = channels;

		for (i = 0; i < s_numchannels; i++, ch++)
		{
			if (ch->sfx && (ch->leftvol || ch->rightvol))
			{
				Com_Printf("%3i %3i %s\n", ch->leftvol,
					ch->rightvol, ch->sfx->name);
				total++;
			}
		}

		Com_Printf("----(%i)---- painted: %i\n", total, paintedtime);
	}

	/* stream music / cinematic audio */
	OGG_Stream();
}

/*
 * ---------------------------------------------------------------
 * misc backend entry points
 * ---------------------------------------------------------------
 */

int
SDL_DriftBeginofs(float timeofs)
{
	int start = (int)(cl.frame.servertime * 0.001f * sound.speed + beginofs);

	if (start < paintedtime)
	{
		start = paintedtime;
		beginofs = (int)(start - (cl.frame.servertime * 0.001f * sound.speed));
	}
	else if (start > paintedtime + 0.3f * sound.speed)
	{
		start = (int)(paintedtime + 0.1f * sound.speed);
		beginofs = (int)(start - (cl.frame.servertime * 0.001f * sound.speed));
	}
	else
	{
		beginofs -= 10;
	}

	return timeofs ? start + timeofs * sound.speed : paintedtime;
}

void
SDL_ClearBuffer(void)
{
	int i;

	for (i = 0; i < MAX_CHANNELS; i++)
	{
		OF_SND_StopChannelVoice(i);
	}

	OF_SND_StopLoops();
}

void
SDL_SoundInfo(void)
{
	Com_Printf("openfpgaOS hardware mixer: %d voices at %d Hz, "
		"native-rate samples\n",
		OF_MIXER_MAX_VOICES, OF_MIXER_OUTPUT_RATE);
}

qboolean
SDL_BackendInit(void)
{
	int i;

	of_audio_init();
	of_mixer_init(OF_MIXER_MAX_VOICES, OF_MIXER_OUTPUT_RATE);

	for (i = 0; i < MAX_CHANNELS; i++)
	{
		snd_handle[i] = OF_MIXER_HANDLE_INVALID;
		snd_shadow_sfx[i] = NULL;
		snd_shadow_end[i] = 0;
	}

	memset(snd_loops, 0, sizeof(snd_loops));
	snd_raw_rate = 0;
	beginofs = 0;

	/* the engine's sample clock runs at this rate; sample playback is
	 * native-rate per voice so this only affects channel bookkeeping */
	sound.speed = 22050;
	sound.channels = 2;
	sound.samples = 0;
	sound.samplebits = 16;
	sound.submission_chunk = 1;
	sound.samplepos = 0;
	sound.buffer = NULL;

	s_numchannels = MAX_CHANNELS;
	if (s_volume)
	{
		s_volume->modified = true;
	}
	snd_inited = true;

	Com_Printf("openfpgaOS hardware mixer sound backend initialized.\n");

	return true;
}

void
SDL_BackendShutdown(void)
{
	SDL_ClearBuffer();
	of_mixer_stop_all();
	snd_inited = false;
	Com_Printf("openfpgaOS hardware mixer sound backend shut down.\n");
}
