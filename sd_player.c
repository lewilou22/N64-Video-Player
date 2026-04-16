/**
 * SD FMV player — libdragon preview. Lists .m1v/.h264 (hides .wav64 in browser).
 * Plays with audio if matching .wav64 exists; otherwise video-only.
 */
#include <malloc.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>
#include <libdragon.h>

#define AUDIO_HZ 32000.0f
#define MENU_LINES 7
#define SEEK_SEC 10.0f
#define FONT_UI 10

/* Deep purple menu background (RGB) */
#define PURP_R 72
#define PURP_G 28
#define PURP_B 108

typedef struct {
    uint32_t type;
    bool has_wav64; /* valid for video files: sidecar audio present */
    char filename[MAX_FILENAME_LEN + 1];
    char fullpath[MAX_FILENAME_LEN + 1];
} direntry_t;

typedef struct {
    bool paused;
    int hold_lr;
} osd_ctx_t;

static char g_dir[512] = "sd:/";
static char g_boot_video[512];
static rdpq_font_t *g_font;
static bool g_menu_ui_open;

static bool file_exists(const char *path)
{
    struct stat st;
    return path && stat(path, &st) == 0;
}

static bool is_wav_sidecar(const char *name)
{
    const char *dot = strrchr(name, '.');
    if (!dot)
        return false;
    return strcasecmp(dot, ".wav64") == 0 || strcasecmp(dot, ".wav") == 0;
}

static bool is_video_file(const char *name)
{
    const char *dot = strrchr(name, '.');
    if (!dot)
        return false;
    return strcasecmp(dot, ".m1v") == 0 || strcasecmp(dot, ".h264") == 0;
}

static bool has_matching_wav64(const char *path)
{
    char wavpath[512];
    const char *ext = strrchr(path, '.');
    if (!ext)
        return false;
    size_t bl = (size_t)(ext - path);
    if (bl + 8 >= sizeof(wavpath))
        return false;
    memcpy(wavpath, path, bl);
    strcpy(wavpath + bl, ".wav64");
    return file_exists(wavpath);
}

/* Hide split-chunk parts 2+ from list (auto-chained on play). */
static bool is_chunk_continuation_file(const char *name)
{
    const char *dot = strrchr(name, '.');
    if (!dot)
        return false;
    const char *tokens[] = {"_part", "-part", ".part"};
    for (unsigned i = 0; i < sizeof(tokens) / sizeof(tokens[0]); i++) {
        const char *tok = strstr(name, tokens[i]);
        if (!tok || tok > dot)
            continue;
        const char *digits = tok + strlen(tokens[i]);
        if (digits >= dot)
            continue;
        bool any = false;
        for (const char *p = digits; p < dot; p++) {
            if (*p < '0' || *p > '9') {
                any = false;
                break;
            }
            any = true;
        }
        if (!any)
            continue;
        long cur = strtol(digits, NULL, 10);
        return cur > 1;
    }
    return false;
}

static void chdir_sd(const char *dirent)
{
    if (strcmp(dirent, "..") == 0) {
        if (strcmp(g_dir, "sd:/") == 0)
            return;
        size_t len = strlen(g_dir);
        while (len > 0 && g_dir[len - 1] == '/')
            g_dir[--len] = '\0';
        char *slash = strrchr(g_dir, '/');
        if (!slash || slash <= g_dir + 2) {
            strcpy(g_dir, "sd:/");
            return;
        }
        if (slash == g_dir + 3) {
            strcpy(g_dir, "sd:/");
            return;
        }
        *slash = '\0';
        strcat(g_dir, "/");
    } else {
        size_t n = strlen(g_dir);
        if (n > 0 && g_dir[n - 1] != '/')
            strcat(g_dir, "/");
        strcat(g_dir, dirent);
        strcat(g_dir, "/");
    }
}

static int dirent_compare(const void *a, const void *b)
{
    const direntry_t *x = a;
    const direntry_t *y = b;
    if (x->type == DT_DIR && y->type != DT_DIR)
        return -1;
    if (x->type != DT_DIR && y->type == DT_DIR)
        return 1;
    return strcmp(x->filename, y->filename);
}

static direntry_t *populate_dir(int *count)
{
    direntry_t *list = malloc(sizeof(direntry_t));
    *count = 0;
    if (!list)
        return NULL;

    dir_t buf;
    int ret = dir_findfirst(g_dir, &buf);
    if (ret != 0) {
        free(list);
        *count = 0;
        return NULL;
    }

    int cap = 1;
    while (ret == 0) {
        if (is_wav_sidecar(buf.d_name)) {
            ret = dir_findnext(g_dir, &buf);
            continue;
        }
        bool take_dir = (buf.d_type == DT_DIR);
        bool take_vid = is_video_file(buf.d_name) && !is_chunk_continuation_file(buf.d_name);
        if (!take_dir && !take_vid) {
            ret = dir_findnext(g_dir, &buf);
            continue;
        }

        if (*count >= cap) {
            cap *= 2;
            direntry_t *nl = realloc(list, sizeof(direntry_t) * cap);
            if (!nl) {
                free(list);
                *count = 0;
                return NULL;
            }
            list = nl;
        }
        list[*count].type = buf.d_type;
        list[*count].has_wav64 = false;
        strncpy(list[*count].filename, buf.d_name, MAX_FILENAME_LEN);
        list[*count].filename[MAX_FILENAME_LEN] = '\0';
        size_t gl = strlen(g_dir);
        size_t nl = strlen(buf.d_name);
        if (gl + nl >= sizeof(list[*count].fullpath)) {
            ret = dir_findnext(g_dir, &buf);
            continue;
        }
        memcpy(list[*count].fullpath, g_dir, gl);
        memcpy(list[*count].fullpath + gl, buf.d_name, nl + 1);
        if (take_vid)
            list[*count].has_wav64 = has_matching_wav64(list[*count].fullpath);
        (*count)++;
        ret = dir_findnext(g_dir, &buf);
    }

    if (*count > 0)
        qsort(list, *count, sizeof(direntry_t), dirent_compare);
    return list;
}

static void free_dir(direntry_t *d)
{
    free(d);
}

static void scroll_fix(int *cursor, int *page, int count)
{
    if (count <= 0) {
        *cursor = 0;
        *page = 0;
        return;
    }
    if (*cursor >= count)
        *cursor = count - 1;
    if (*cursor < 0)
        *cursor = 0;
    if (*cursor < *page)
        *page = *cursor;
    if (*cursor >= *page + MENU_LINES)
        *page = (*cursor - MENU_LINES) + 1;
}

static void menu_ui_init(void)
{
    display_init(RESOLUTION_320x240, DEPTH_16_BPP, 2, GAMMA_NONE, FILTERS_DISABLED);
    rdpq_init();
    if (!g_font)
        g_font = rdpq_font_load_builtin(FONT_BUILTIN_DEBUG_MONO);
    rdpq_text_register_font(FONT_UI, g_font);
    g_menu_ui_open = true;
}

static void menu_ui_shutdown(void)
{
    if (!g_menu_ui_open)
        return;
    /* rdpq_close does not clear rdpq_text font slots; must unregister or next register asserts. */
    rdpq_text_unregister_font(FONT_UI);
    rdpq_close();
    display_close();
    g_menu_ui_open = false;
}

static void draw_menu(direntry_t *list, int cursor, int page, int count)
{
    surface_t *disp = display_get();
    uint32_t bg = graphics_make_color(PURP_R, PURP_G, PURP_B, 255);
    graphics_fill_screen(disp, bg);

    rdpq_attach(disp, NULL);

    rdpq_text_printf(NULL, FONT_UI, 10, 10, "SD CINEMA");
    rdpq_text_printf(NULL, FONT_UI, 10, 24, "%s", g_dir);

    int y = 42;

    if (!list || count == 0) {
        rdpq_text_printf(NULL, FONT_UI, 8, y, "(empty) Add .m1v / .h264  ~ = no .wav64");
    } else {
        int maxl = MENU_LINES;
        if (maxl > count)
            maxl = count;
        scroll_fix(&cursor, &page, count);
        for (int i = 0; i < maxl; i++) {
            int idx = page + i;
            if (idx >= count)
                break;
            bool sel = (idx == cursor);
            int rowy = y + i * 14;
            if (list[idx].type == DT_DIR)
                rdpq_text_printf(NULL, FONT_UI, 12, rowy, "%s [%s]", sel ? ">>" : "  ", list[idx].filename);
            else
                rdpq_text_printf(NULL, FONT_UI, 12, rowy, "%s %s%s", sel ? ">>" : "  ", list[idx].filename,
                                 list[idx].has_wav64 ? "" : " ~");
        }
    }

    rdpq_detach_show();
}

static bool load_boot_cfg(void)
{
    const char *cfg[] = {"sd:/ED64P/VIDEO.CFG", "sd:/ED64/VIDEO.CFG"};
    char line[384];
    for (unsigned i = 0; i < sizeof(cfg) / sizeof(cfg[0]); i++) {
        FILE *fp = fopen(cfg[i], "r");
        if (!fp)
            continue;
        bool got = false;
        while (fgets(line, sizeof(line), fp)) {
            char *s = line;
            while (*s == ' ' || *s == '\t')
                s++;
            if (!*s || *s == '#' || *s == '\n')
                continue;
            size_t n = strlen(s);
            while (n && (s[n - 1] == '\n' || s[n - 1] == '\r'))
                s[--n] = '\0';
            if (strncmp(s, "video=", 6) == 0) {
                snprintf(g_boot_video, sizeof(g_boot_video), "%s", s + 6);
                got = true;
            } else if (strncmp(s, "sd:/", 4) == 0 || strncmp(s, "rom:/", 5) == 0) {
                snprintf(g_boot_video, sizeof(g_boot_video), "%s", s);
                got = true;
            }
        }
        fclose(fp);
        unlink(cfg[i]);
        if (got && file_exists(g_boot_video))
            return true;
    }
    return false;
}

static const char *find_last_substr_before(const char *hay, const char *needle, const char *limit)
{
    const char *last = NULL;
    size_t nlen = strlen(needle);
    const char *p = hay;
    while ((p = strstr(p, needle)) != NULL) {
        if (p + nlen > limit)
            break;
        last = p;
        p++;
    }
    return last;
}

static bool build_next_chunk_path(const char *path, char *out, size_t out_sz)
{
    const char *ext = strrchr(path, '.');
    if (!ext)
        return false;
    const char *tok = NULL;
    const char *tok_pos = NULL;
    const char *tokens[] = {"_part", "-part", ".part"};
    for (unsigned i = 0; i < sizeof(tokens) / sizeof(tokens[0]); i++) {
        const char *p = find_last_substr_before(path, tokens[i], ext);
        if (p) {
            tok = tokens[i];
            tok_pos = p;
            break;
        }
    }
    if (!tok_pos)
        return false;
    const char *digits = tok_pos + strlen(tok);
    if (digits >= ext)
        return false;
    for (const char *p = digits; p < ext; p++) {
        if (*p < '0' || *p > '9')
            return false;
    }
    int width = (int)(ext - digits);
    if (width < 2 || width > 6)
        return false;
    long cur = strtol(digits, NULL, 10);
    if (cur < 0 || cur > 999998)
        return false;
    long next = cur + 1;
    size_t prefix_len = (size_t)(digits - path);
    if (prefix_len >= out_sz)
        return false;
    int n = snprintf(out, out_sz, "%.*s%0*ld%s", (int)prefix_len, path, width, next, ext);
    return n > 0 && (size_t)n < out_sz;
}

static void osd_playback(void *ctx, int frame_idx, float time_sec, fmv_control_t *ctrl)
{
    (void)frame_idx;
    osd_ctx_t *oc = (osd_ctx_t *)ctx;

    joypad_poll();
    joypad_buttons_t pr = joypad_get_buttons_pressed(JOYPAD_PORT_1);
    joypad_buttons_t hd = joypad_get_buttons_held(JOYPAD_PORT_1);

    if (pr.a) {
        oc->paused = !oc->paused;
        ctrl->pause(ctrl, oc->paused);
    }
    if (pr.b)
        ctrl->stop(ctrl);

    if (pr.d_left)
        ctrl->seek_time(ctrl, time_sec - SEEK_SEC, false);
    if (pr.d_right)
        ctrl->seek_time(ctrl, time_sec + SEEK_SEC, false);

    if (hd.d_left && !hd.d_right) {
        oc->hold_lr++;
        if (oc->hold_lr > 28 && (oc->hold_lr % 10) == 0)
            ctrl->seek_time(ctrl, time_sec - SEEK_SEC, false);
    } else if (hd.d_right && !hd.d_left) {
        oc->hold_lr++;
        if (oc->hold_lr > 28 && (oc->hold_lr % 10) == 0)
            ctrl->seek_time(ctrl, time_sec + SEEK_SEC, false);
    } else
        oc->hold_lr = 0;

    if (oc->paused) {
        int w = display_get_width();
        int h = display_get_height();
        rdpq_text_printf(NULL, FONT_UI, (w / 2) - 28, (h / 2) - 6, "PAUSED");
    }
}

static void play_one(const char *path)
{
    bool audio_ok = has_matching_wav64(path);

    osd_ctx_t octx = {0};
    fmv_parms_t parms = {
        .osd_callback = osd_playback,
        .osd_ctx = &octx,
        .disable_audio = !audio_ok,
        .disable_subtitles = true,
        /* Fast carts: decode every frame; no drop-to-catch-audio mode. */
        .disable_frame_skipping = true,
        .crt_margin = true,
    };

    if (g_menu_ui_open)
        menu_ui_shutdown();

    if (!g_font)
        g_font = rdpq_font_load_builtin(FONT_BUILTIN_DEBUG_MONO);
    rdpq_init();
    rdpq_text_register_font(FONT_UI, g_font);
    fmv_play(path, &parms);
    rdpq_text_unregister_font(FONT_UI);
    rdpq_close();

    menu_ui_init();
}

static void play_sequence(const char *path)
{
    char current[512];
    char next[512];
    snprintf(current, sizeof(current), "%s", path);

    while (1) {
        play_one(current);
        if (!build_next_chunk_path(current, next, sizeof(next)) || !file_exists(next))
            break;
        snprintf(current, sizeof(current), "%s", next);
    }
}

int main(void)
{
    debug_init_isviewer();
    debug_init_usblog();
    joypad_init();

    if (dfs_init(DFS_DEFAULT_LOCATION) != DFS_ESUCCESS) { }

    if (!debug_init_sdfs("sd:/", -1)) {
        display_init(RESOLUTION_320x240, DEPTH_16_BPP, 2, GAMMA_NONE, FILTERS_DISABLED);
        rdpq_init();
        if (!g_font)
            g_font = rdpq_font_load_builtin(FONT_BUILTIN_DEBUG_MONO);
        rdpq_text_register_font(FONT_UI, g_font);
        surface_t *disp = display_get();
        graphics_fill_screen(disp, graphics_make_color(PURP_R, PURP_G, PURP_B, 255));
        rdpq_attach(disp, NULL);
        rdpq_text_printf(NULL, FONT_UI, 24, 100, "SD card not available");
        rdpq_detach_show();
        display_show(disp);
        while (1) {
            joypad_poll();
            wait_ms(50);
        }
    }

    audio_init(AUDIO_HZ, 4);
    mixer_init(8);
    video_register_codec(&mpeg1_codec);
    video_register_codec(&h264_codec);

    if (load_boot_cfg())
        play_sequence(g_boot_video);

    int page = 0;
    int cursor = 0;
    int count = 0;
    direntry_t *list = populate_dir(&count);

    if (!g_menu_ui_open)
        menu_ui_init();

    while (1) {
        if (!list) {
            draw_menu(NULL, 0, 0, 0);
            joypad_poll();
            wait_ms(120);
            list = populate_dir(&count);
            continue;
        }

        draw_menu(list, cursor, page, count);

        joypad_poll();
        joypad_buttons_t k = joypad_get_buttons_pressed(JOYPAD_PORT_1);

        if (k.d_up) {
            cursor--;
            scroll_fix(&cursor, &page, count);
        }
        if (k.d_down) {
            cursor++;
            scroll_fix(&cursor, &page, count);
        }
        if (k.l) {
            cursor -= MENU_LINES;
            if (cursor < 0)
                cursor = 0;
            scroll_fix(&cursor, &page, count);
        }
        if (k.r || k.z) {
            cursor += MENU_LINES;
            if (count > 0 && cursor >= count)
                cursor = count - 1;
            scroll_fix(&cursor, &page, count);
        }
        if (k.b) {
            chdir_sd("..");
            free_dir(list);
            list = populate_dir(&count);
            cursor = 0;
            page = 0;
            continue;
        }
        if ((k.a || k.start) && count > 0) {
            if (list[cursor].type == DT_DIR) {
                chdir_sd(list[cursor].filename);
                free_dir(list);
                list = populate_dir(&count);
                cursor = 0;
                page = 0;
                continue;
            }
            if (is_video_file(list[cursor].filename)) {
                char pathcopy[MAX_FILENAME_LEN + 8];
                snprintf(pathcopy, sizeof(pathcopy), "%s", list[cursor].fullpath);
                char keep[MAX_FILENAME_LEN + 1];
                strncpy(keep, list[cursor].filename, sizeof(keep) - 1);
                keep[sizeof(keep) - 1] = '\0';
                free_dir(list);
                list = NULL;
                play_sequence(pathcopy);
                list = populate_dir(&count);
                for (int i = 0; i < count; i++) {
                    if (strcmp(list[i].filename, keep) == 0) {
                        cursor = i;
                        break;
                    }
                }
                scroll_fix(&cursor, &page, count);
                continue;
            }
        }
        wait_ms(50);
    }
}
