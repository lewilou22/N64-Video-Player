#!/usr/bin/env python3
"""
Convert .wav / .aiff / .mp3 to libdragon .wav64 (audio only — no video, no MIPS toolchain).

Needs audioconv64 only:
  - Put audioconv64.exe next to this script, or
  - Set AUDIOCONV64=C:\\path\\to\\audioconv64.exe, or
  - Pass --tools path-to-folder-or-exe, or
  - Set N64_INST and use bin\\audioconv64.exe, or
  - Have audioconv64 on PATH

Docs: https://github.com/DragonMinded/libdragon/wiki/Installing-libdragon
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from audio2wav64_lib import Wav64ConversionError, convert_many_wav64


def die(msg: str, code: int = 1) -> None:
    print(f"audio2wav64: error: {msg}", file=sys.stderr)
    sys.exit(code)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Convert audio to N64 .wav64 (libdragon audioconv64 only).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="Input .wav / .aiff / .mp3 files",
    )
    p.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("wav64-out"),
        help="Directory for .wav64 output",
    )
    p.add_argument(
        "--tools",
        type=Path,
        default=None,
        metavar="PATH",
        help="audioconv64.exe, or folder containing it, or N64_INST root",
    )
    p.add_argument(
        "--wav-compress",
        choices=("0", "1", "3"),
        default="1",
        help="0=none, 1=vadpcm (default), 3=opus",
    )
    p.add_argument("--wav-mono", action="store_true", help="Force mono output")
    p.add_argument(
        "--wav-resample",
        type=int,
        default=None,
        metavar="HZ",
        help="Resample to this rate (Hz)",
    )
    p.add_argument("--quiet", action="store_true", help="No --verbose for audioconv64")

    args = p.parse_args()
    extra: list[str] = ["--wav-compress", args.wav_compress]
    if args.wav_mono:
        extra.append("--wav-mono")
    if args.wav_resample is not None:
        extra.extend(["--wav-resample", str(args.wav_resample)])

    try:
        convert_many_wav64(
            list(args.inputs),
            args.output_dir,
            tools_path=args.tools,
            extra_audioconv_args=extra,
            verbose=not args.quiet,
            log=print,
            cancel=None,
        )
    except Wav64ConversionError as e:
        die(str(e))

    print("Done.")


if __name__ == "__main__":
    main()
