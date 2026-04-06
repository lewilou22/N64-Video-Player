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

from n64fmv_lib import (
    ConversionError,
    ConvertOptions,
    convert_many,
    convert_to_chunk_rom_pack,
    convert_to_menu_rom_bundle,
    convert_to_single_rom_fit,
)


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
    p.add_argument("--fps", type=int, default=24, help="Target frame rate")
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
    p.add_argument(
        "--chunk-seconds",
        type=float,
        default=None,
        metavar="SEC",
        help="Split one input into sequential *_part001 files",
    )
    p.add_argument(
        "--chunk-auto",
        action="store_true",
        help="Auto-calculate chunk size for long episodes",
    )
    p.add_argument(
        "--no-fit-sd-preload",
        action="store_true",
        help="Do not clamp chunk size to SD preload threshold",
    )
    p.add_argument(
        "--sd-profile",
        choices=("default", "v2"),
        default="default",
        help="SD tuning profile (use v2 for stricter chunk sizing)",
    )
    p.add_argument(
        "--no-auto-tune",
        action="store_true",
        help="Disable long-video auto-tuning (bitrate/FPS/audio caps)",
    )
    p.add_argument(
        "--no-force-cbr",
        action="store_true",
        help="Disable CBR-ish MPEG rate control (minrate/maxrate/bufsize)",
    )
    p.add_argument(
        "--rom-pack",
        action="store_true",
        help="Build one .z64 ROM per chunk (single input only)",
    )
    p.add_argument(
        "--menu-rom-bundle",
        action="store_true",
        help="Build seamless SD menu bundle (videos + SDVIDEO.Z64 + config template)",
    )
    p.add_argument(
        "--fit-rom-mb",
        type=float,
        default=None,
        metavar="MB",
        help="Build single embedded ROM that fits this max size (best quality first)",
    )
    p.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Project root used for ROM building (contains Makefile/filesystem)",
    )

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
        auto_tune_long_videos=not args.no_auto_tune,
        force_cbr=not args.no_force_cbr,
        chunk_seconds=args.chunk_seconds,
        chunk_auto=args.chunk_auto,
        fit_sd_preload=not args.no_fit_sd_preload,
        sd_profile=args.sd_profile,
    )
    try:
        if args.rom_pack:
            if len(args.inputs) != 1:
                raise ConversionError("--rom-pack requires exactly one input file.")
            convert_to_chunk_rom_pack(
                args.inputs[0],
                args.output_dir,
                repo_root=args.repo_root,
                n64_inst=args.n64_inst,
                stem_override=args.stem,
                opts=opts,
            )
        elif args.menu_rom_bundle:
            if len(args.inputs) != 1:
                raise ConversionError("--menu-rom-bundle requires exactly one input file.")
            convert_to_menu_rom_bundle(
                args.inputs[0],
                args.output_dir,
                repo_root=args.repo_root,
                n64_inst=args.n64_inst,
                stem_override=args.stem,
                opts=opts,
                build_engine_rom=True,
            )
        elif args.fit_rom_mb is not None:
            if len(args.inputs) != 1:
                raise ConversionError("--fit-rom-mb requires exactly one input file.")
            convert_to_single_rom_fit(
                args.inputs[0],
                args.output_dir,
                repo_root=args.repo_root,
                n64_inst=args.n64_inst,
                stem_override=args.stem,
                opts=opts,
                max_rom_mb=args.fit_rom_mb,
                rom_filename="n64video.z64",
            )
        else:
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
