"""
Shared conversion logic for N64 FMV (.m1v + .wav64). Used by video2n64 CLI and GUI.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class ConversionError(Exception):
    pass


LogFn = Callable[[str], None]
CancelFn = Callable[[], bool] | None


def which_ffmpeg() -> str:
    p = shutil.which("ffmpeg")
    if not p:
        raise ConversionError("ffmpeg not found in PATH. Install ffmpeg and try again.")
    return p


def find_audioconv64(n64_inst: Path | None) -> Path | None:
    if n64_inst:
        p = n64_inst / "bin" / "audioconv64"
        if p.is_file() and os.access(p, os.X_OK):
            return p
        return None
    env = os.environ.get("N64_INST", "").strip()
    if env:
        p = Path(env) / "bin" / "audioconv64"
        if p.is_file() and os.access(p, os.X_OK):
            return p
    home = Path.home()
    for candidate in (
        home / "libdragon-n64-inst" / "bin" / "audioconv64",
        Path("/opt/libdragon/bin/audioconv64"),
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def stem_from_input(path: Path) -> str:
    s = path.stem or "output"
    return s.replace(" ", "_")[:200]


def build_time_args(start: float | None, duration: float | None) -> list[str]:
    out: list[str] = []
    if start is not None and start > 0:
        out.extend(["-ss", str(start)])
    if duration is not None and duration > 0:
        out.extend(["-t", str(duration)])
    return out


def build_vf(scale: str, vf_extra: str | None) -> str:
    s = (vf_extra or "").strip()
    if s.startswith(","):
        s = s[1:].strip()
    if s:
        return f"scale={scale},{s}"
    return f"scale={scale}"


def run_ffmpeg(
    ffmpeg: str,
    ff_args: list[str],
    label: str,
    *,
    log: LogFn,
    cancel: CancelFn = None,
) -> None:
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", *ff_args]
    log(f"  [{label}]")
    log("  " + " ".join(cmd))
    if cancel and cancel():
        raise ConversionError("Cancelled.")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        if cancel and cancel():
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise ConversionError("Cancelled.")
        line = line.rstrip()
        if line:
            log("  | " + line)
    proc.wait()
    if proc.returncode != 0:
        raise ConversionError(f"ffmpeg failed ({label}), exit {proc.returncode}")


@dataclass
class ConvertOptions:
    video_bitrate: str = "800K"
    fps: int = 20
    scale: str = "320:-16"
    """ffmpeg scale= width:height expression."""
    vf_extra: str = ""
    """Extra filters after scale, e.g. eq=gamma=0.4545 (no leading comma)."""
    audio_hz: int = 32000
    start_sec: float | None = None
    duration_sec: float | None = None
    no_audio: bool = False
    keep_wav: bool = False
    skip_wav64: bool = False


def convert_one(
    input_path: Path,
    out_dir: Path,
    stem: str,
    *,
    ffmpeg: str,
    audioconv: Path | None,
    opts: ConvertOptions,
    log: LogFn = print,
    cancel: CancelFn = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    m1v = out_dir / f"{stem}.m1v"
    wav = out_dir / f"{stem}.wav"
    tflags = build_time_args(opts.start_sec, opts.duration_sec)
    vf = build_vf(opts.scale, opts.vf_extra or None)

    run_ffmpeg(
        ffmpeg,
        [
            *tflags,
            "-i",
            str(input_path),
            "-vb",
            opts.video_bitrate,
            "-vf",
            vf,
            "-r",
            str(opts.fps),
            str(m1v),
        ],
        "video -> m1v",
        log=log,
        cancel=cancel,
    )

    if opts.no_audio:
        log("  [skip] audio (no-audio)")
        log(f"  -> {m1v}")
        return

    run_ffmpeg(
        ffmpeg,
        [
            *tflags,
            "-i",
            str(input_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(opts.audio_hz),
            "-ac",
            "1",
            str(wav),
        ],
        "audio -> wav",
        log=log,
        cancel=cancel,
    )

    if opts.skip_wav64:
        log(f"  [skip] wav64; run: audioconv64 -o {out_dir} {wav}")
        log(f"  -> {m1v}")
        log(f"  -> {wav}")
        return

    if not audioconv:
        log(
            "  [warn] audioconv64 not found; .wav only. Set N64_INST or pick install folder."
        )
        log(f"  -> {m1v}")
        log(f"  -> {wav}")
        return

    cmd = [str(audioconv), "-o", str(out_dir), "--verbose", str(wav)]
    log(f"  [wav64]")
    log("  " + " ".join(cmd))
    if cancel and cancel():
        raise ConversionError("Cancelled.")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        if cancel and cancel():
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise ConversionError("Cancelled.")
        line = line.rstrip()
        if line:
            log("  | " + line)
    proc.wait()
    if proc.returncode != 0:
        raise ConversionError(f"audioconv64 failed, exit {proc.returncode}")

    if not opts.keep_wav:
        try:
            wav.unlink()
        except OSError as e:
            log(f"  [warn] could not remove {wav}: {e}")

    log(f"  -> {m1v}")
    if (out_dir / f"{stem}.wav64").is_file():
        log(f"  -> {out_dir / (stem + '.wav64')}")


def convert_many(
    inputs: list[Path],
    out_dir: Path,
    *,
    n64_inst: Path | None,
    stem_override: str | None,
    opts: ConvertOptions,
    log: LogFn = print,
    cancel: CancelFn = None,
) -> None:
    ffmpeg = which_ffmpeg()
    audioconv = (
        None
        if opts.skip_wav64 or opts.no_audio
        else find_audioconv64(n64_inst)
    )
    out_dir = out_dir.resolve()
    log(f"Output directory: {out_dir}")
    if audioconv:
        log(f"audioconv64: {audioconv}")
    else:
        log("audioconv64: (not found — .wav64 skipped unless you set toolchain path)")
    log("")

    for i, inp in enumerate(inputs):
        inp = inp.expanduser().resolve()
        if not inp.is_file():
            raise ConversionError(f"not a file: {inp}")
        if stem_override is not None and len(inputs) > 1:
            raise ConversionError("Custom output name only works with a single file.")
        stem = stem_override if stem_override else stem_from_input(inp)
        if len(inputs) > 1:
            stem = stem_from_input(inp)
        log(f"[{i + 1}/{len(inputs)}] {inp.name}")
        convert_one(
            inp,
            out_dir,
            stem,
            ffmpeg=ffmpeg,
            audioconv=audioconv,
            opts=opts,
            log=log,
            cancel=cancel,
        )
        log("")
