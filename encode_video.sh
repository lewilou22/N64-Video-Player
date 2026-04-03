#!/usr/bin/env sh
# Encode a host video into libdragon FMV inputs (MPEG-1 elementary + PCM WAV).
# See: https://github.com/DragonMinded/libdragon/wiki/MPEG1-Player
set -e
INPUT="${1:?usage: encode_video.sh <input.mp4>}"
OUTDIR="${2:-filesystem}"
mkdir -p "$OUTDIR"

echo "Encoding video -> $OUTDIR/movie.m1v (320-wide, ~800k, 20 fps)"
ffmpeg -y -i "$INPUT" -vb 800K -vf 'scale=320:-16' -r 20 "$OUTDIR/movie.m1v"

echo "Extracting audio -> $OUTDIR/movie.wav (32 kHz mono PCM)"
ffmpeg -y -i "$INPUT" -vn -acodec pcm_s16le -ar 32000 -ac 1 "$OUTDIR/movie.wav"

echo "Done. Run: \"\$N64_INST/bin/audioconv64\" \"$OUTDIR/movie.wav\""
echo "Then: make"
