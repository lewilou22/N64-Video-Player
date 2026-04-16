# Requires libdragon unstable (preview) — FMV / MPEG-1 player is not on trunk yet.
# Install toolchain: https://github.com/DragonMinded/libdragon/wiki/Installing-libdragon
# Set N64_INST to your mips64 toolchain prefix.

BUILD_DIR = build
include $(N64_INST)/include/n64.mk

N64_ROM_TITLE = N64 Video
N64_ROM_CATEGORY = N
N64_ROM_SAVETYPE = none

VIDEO_SRC ?= assets/input.mp4

src = main.c

DFS_FILES = filesystem/movie.m1v
ifneq ($(wildcard filesystem/movie.wav64),)
DFS_FILES += filesystem/movie.wav64
endif

all: n64video.z64

# SD player uses its own makefile (separate BUILD_DIR; see Makefile.sd)
.PHONY: sdvideo
sdvideo:
	$(MAKE) -f Makefile.sd

# lantus-style 4-viewport OpenGL demo (not SM64); see splitscreen-demo/README.md
.PHONY: splitscreen
splitscreen:
	$(MAKE) -C splitscreen-demo

# Libdragon FMV engine for Altra64: copy sdvideo.z64 next to EverDrive firmware (see ALTRA64_INTEGRATION.md).
.PHONY: video-engine-altra64
video-engine-altra64: sdvideo
	@echo "Built: $(CURDIR)/sdvideo.z64"
	@echo "On SD (ED64+):  ED64P/ENGINES/SDVIDEO.Z64  (or ED64P/SDVIDEO.Z64)"
	@echo "On SD (classic): ED64/ENGINES/SDVIDEO.Z64 — build Altra64: make -C vendor/altra64 ALTRA_ED64_CLASSIC=1 (Docker)"

# Optional: stage engine onto a mounted FAT32 tree, e.g. make stage-altra64-engine ALTRA64_SD_ROOT=/mnt/sdcard
.PHONY: stage-altra64-engine stage-altra64-engine-classic
stage-altra64-engine: sdvideo
	@test -n "$(ALTRA64_SD_ROOT)" || (echo "Set ALTRA64_SD_ROOT to mounted SD root (e.g. /mnt/sdcard)"; exit 1)
	mkdir -p "$(ALTRA64_SD_ROOT)/ED64P/ENGINES"
	cp -f sdvideo.z64 "$(ALTRA64_SD_ROOT)/ED64P/ENGINES/SDVIDEO.Z64"
	@echo "Installed $(ALTRA64_SD_ROOT)/ED64P/ENGINES/SDVIDEO.Z64"

stage-altra64-engine-classic: sdvideo
	@test -n "$(ALTRA64_SD_ROOT)" || (echo "Set ALTRA64_SD_ROOT to mounted SD root (e.g. G:\\ or /mnt/sdcard)"; exit 1)
	mkdir -p "$(ALTRA64_SD_ROOT)/ED64/ENGINES"
	cp -f sdvideo.z64 "$(ALTRA64_SD_ROOT)/ED64/ENGINES/SDVIDEO.Z64"
	@echo "Installed $(ALTRA64_SD_ROOT)/ED64/ENGINES/SDVIDEO.Z64"

n64video.z64: N64_ROM_TITLE="Judge Judy Demo"
n64video.z64: $(BUILD_DIR)/n64video.dfs

$(BUILD_DIR)/n64video.elf: $(src:%.c=$(BUILD_DIR)/%.o)

$(BUILD_DIR)/n64video.dfs: $(DFS_FILES)
	@echo " [DFS] $@"
	$(N64_MKDFS) "$@" filesystem >/dev/null

# --- Asset pipeline (host: ffmpeg + audioconv64) ---

.PHONY: encode audioconv

encode:
	@mkdir -p filesystem assets
	@if [ ! -f "$(VIDEO_SRC)" ]; then \
		echo "Place a source video at $(VIDEO_SRC) or run: make encode VIDEO_SRC=/path/to/file.mp4"; \
		exit 1; \
	fi
	./scripts/encode_video.sh "$(VIDEO_SRC)"

# After encode, if movie.wav exists, convert to wav64 for FMV audio
audioconv:
	@if [ ! -f "filesystem/movie.wav" ]; then \
		echo "Missing filesystem/movie.wav (run: make encode VIDEO_SRC=/path/to/file.mp4)"; \
		exit 1; \
	fi
	"$(N64_INST)/bin/audioconv64" -o filesystem --verbose "filesystem/movie.wav"

clean:
	rm -rf $(BUILD_DIR) n64video.z64
	$(MAKE) -f Makefile.sd clean 2>/dev/null || true
	$(MAKE) -C splitscreen-demo clean 2>/dev/null || true
# Intentionally keep filesystem/* so `make clean && make` still works.

clean-assets: clean
	rm -f filesystem/movie.m1v filesystem/movie.wav filesystem/movie.wav64

-include $(wildcard $(BUILD_DIR)/*.d)

.PHONY: all sdvideo splitscreen video-engine-altra64 stage-altra64-engine stage-altra64-engine-classic clean clean-assets encode audioconv
