/**
 * N64 video player using libdragon (unstable / preview FMV API).
 *
 * Expects rom:/movie.m1v (raw MPEG-1 elementary stream) and optionally
 * rom:/movie.wav64 for audio. Encode with scripts/encode_video.sh + audioconv64.
 */
#include <libdragon.h>
#include <stdbool.h>

#define AUDIO_HZ 32000.0f

typedef enum {
    PLAYBACK_BALANCED = 0,
    PLAYBACK_SMOOTH = 1,
} playback_mode_t;

static const char *playback_mode_name(playback_mode_t mode)
{
    return mode == PLAYBACK_SMOOTH ? "Smooth (audio off)" : "Balanced";
}

static const char *colorspace_name(const yuv_colorspace_t *cs)
{
    if (memcmp(cs, &YUV_BT601_TV, sizeof(yuv_colorspace_t)) == 0) {
        return "BT.601 TV";
    }
    if (memcmp(cs, &YUV_BT601_FULL, sizeof(yuv_colorspace_t)) == 0) {
        return "BT.601 Full";
    }
    if (memcmp(cs, &YUV_BT709_TV, sizeof(yuv_colorspace_t)) == 0) {
        return "BT.709 TV";
    }
    if (memcmp(cs, &YUV_BT709_FULL, sizeof(yuv_colorspace_t)) == 0) {
        return "BT.709 Full";
    }
    return "Unknown";
}

static void osd_callback(void *ctx, int frame_idx, float time_sec, fmv_control_t *ctrl)
{
    (void)ctx;

    if (frame_idx == 0) {
        video_info_t info = video_get_info(ctrl->video);
        debugf("Video: %dx%d DAR=%.2f @ %.2f fps — %s\n",
               info.width, info.height, info.aspect_ratio, info.framerate,
               colorspace_name(&info.colorspace));
    }

    // Poll input every other frame to minimize callback overhead.
    if ((frame_idx & 1) != 0)
        return;
    joypad_poll();
    joypad_buttons_t pressed = joypad_get_buttons_pressed(JOYPAD_PORT_1);
    if (pressed.start || pressed.a) {
        ctrl->stop(ctrl);
    }
}

int main(void)
{
    debug_init_isviewer();
    debug_init_usblog();

    dfs_init(DFS_DEFAULT_LOCATION);
    rdpq_init();
    yuv_init();
    joypad_init();

    audio_init(AUDIO_HZ, 4);
    mixer_init(8);

    video_register_codec(&mpeg1_codec);

    FILE *f = fopen("rom:/movie.m1v", "rb");
    assertf(f,
            "Missing rom:/movie.m1v\n"
            "Put a video in assets/input.mp4 (or set VIDEO_SRC) and run:\n"
            "  ./scripts/encode_video.sh\n"
            "  make audioconv  # optional: movie.wav -> movie.wav64\n"
            "  make\n");
    fclose(f);

    fmv_parms_t parms = {
        .osd_callback = osd_callback,
    };

    FILE *wav64 = fopen("rom:/movie.wav64", "rb");
    bool has_wav64 = wav64 != NULL;
    if (wav64)
        fclose(wav64);

    console_init();
    console_set_render_mode(RENDER_MANUAL);
    playback_mode_t mode = PLAYBACK_SMOOTH;

    while (1) {
        console_clear();
        printf("N64 Video ROM\n");
        printf("----------------------------------------\n");
        printf("Video: rom:/movie.m1v\n");
        printf("Audio: %s\n", has_wav64 ? "rom:/movie.wav64 found" : "not found");
        printf("Mode : %s\n", playback_mode_name(mode));
        printf("----------------------------------------\n");
        printf("A/START: Play\n");
        printf("Left/Right: Toggle playback mode\n");
        printf("B: Exit ROM\n");
        printf("\nSmooth mode disables audio to reduce lag on heavy clips.\n");
        console_render();

        joypad_poll();
        joypad_buttons_t k = joypad_get_buttons_pressed(JOYPAD_PORT_1);
        if (k.d_left || k.d_right) {
            mode = (mode == PLAYBACK_BALANCED) ? PLAYBACK_SMOOTH : PLAYBACK_BALANCED;
        }
        if (k.b) {
            return 0;
        }
        if (k.a || k.start) {
            break;
        }
        wait_ms(50);
    }

    console_close();
    parms.disable_audio = (mode == PLAYBACK_SMOOTH) || !has_wav64;

    fmv_play("rom:/movie.m1v", &parms);

    return 0;
}
