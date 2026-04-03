#!/usr/bin/env python3
"""
Convert video files to libdragon FMV inputs: MPEG-1 elementary (.m1v) + VADPCM (.wav64).

Requires:
  - ffmpeg in PATH
  - audioconv64 from the libdragon preview toolchain (set N64_INST or use --n64-inst)

Docs: https://github.com/DragonMinded/libdragon/wiki/MPEG1-Player
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from n64fmv_lib import ConversionError, ConvertOptions, convert_many


def die(msg: str, code: int = 1) -> None:
    print(f"video2n64: error: {msg}", file=sys.stderr)
    sys.exit(code)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Convert videos to N64 FMV (.m1v + optional .wav64) for libdragon.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("inputs", nargs="+", type=Path, help="Input video files (mp4, mkv, …)")
    p.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("n64-fmv-out"),
        help="Directory for output files",
    )
    p.add_argument(
        "--n64-inst",
        type=Path,
        default=None,
        help="Libdragon install prefix (contains bin/audioconv64). Overrides N64_INST.",
    )
    p.add_argument("--video-bitrate", default="800K", help="ffmpeg video bitrate")
    p.add_argument("--fps", type=int, default=20, help="Target frame rate")
    p.add_argument(
        "--scale",
        default="320:-16",
        help="ffmpeg scale= width:height (-16 = height multiple of 16)",
    )
    p.add_argument(
        "--vf-extra",
        default="",
        help="Extra ffmpeg video filters after scale (e.g. eq=gamma=0.4545)",
    )
    p.add_argument(
        "--audio-hz",
        type=int,
        default=32000,
        help="PCM sample rate for .wav before wav64",
    )
    p.add_argument(
        "--start",
        type=float,
        default=None,
        metavar="SEC",
        help="Start time in seconds (ffmpeg -ss)",
    )
    p.add_argument(
        "--duration",
        type=float,
        default=None,
        metavar="SEC",
        help="Max length in seconds (ffmpeg -t); omit for full file",
    )
    p.add_argument(
        "--stem",
        default=None,
        help="Output base name (single input only)",
    )
    p.add_argument("--no-audio", action="store_true", help="Only produce .m1v")
    p.add_argument("--skip-wav64", action="store_true", help="Stop after .wav")
    p.add_argument("--keep-wav", action="store_true", help="Keep .wav after wav64")

    args = p.parse_args()
    opts = ConvertOptions(
        video_bitrate=args.video_bitrate,
        fps=args.fps,
        scale=args.scale,
        vf_extra=args.vf_extra,
        audio_hz=args.audio_hz,
        start_sec=args.start,
        duration_sec=args.duration,
        no_audio=args.no_audio,
        keep_wav=args.keep_wav,
        skip_wav64=args.skip_wav64,
    )
    try:
        convert_many(
            list(args.inputs),
            args.output_dir,
            n64_inst=args.n64_inst,
            stem_override=args.stem,
            opts=opts,
        )
    except ConversionError as e:
        die(str(e))
    print("Done.")


if __name__ == "__main__":
    main()
