"""
Audio-only WAV/MP3 → .wav64 for libdragon. No video, no full N64 toolchain required.

You only need:
  - audioconv64.exe (ship a small zip next to this app, or point to N64_INST\\bin)
  - optional: ffmpeg if you want to normalize/resample before convert (not required for .wav)

Discovery order for audioconv64:
  1. Env AUDIOCONV64 — full path to audioconv64 or audioconv64.exe
  2. Explicit tools_path passed to find_audioconv64():
       - path to the .exe, or
       - a folder that contains audioconv64.exe, or
       - N64_INST root (uses bin/audioconv64.exe)
  3. Env N64_INST (…/bin/audioconv64.exe)
  4. Same directory as this .py file (bundled copy)
  5. PATH (shutil.which)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

LogFn = Callable[[str], None]
CancelFn = Callable[[], bool] | None


class Wav64ConversionError(Exception):
    pass


def _is_win() -> bool:
    return sys.platform == "win32"


def _audioconv_path_arg(p: Path) -> str:
    """
    audioconv64 on Windows uses strrchr(path, '/') for basename; backslashes
    break -o <dir> <file>. Use forward slashes for arguments we pass to it.
    """
    s = str(p.resolve())
    if _is_win():
        s = s.replace("\\", "/")
    return s


def _exe_ok(p: Path) -> bool:
    if not p.is_file():
        return False
    if _is_win():
        return True
    return os.access(p, os.X_OK)


def _names() -> tuple[str, ...]:
    return ("audioconv64.exe", "audioconv64") if _is_win() else ("audioconv64",)


def find_audioconv64(tools_path: Path | None = None) -> Path | None:
    """
    Resolve audioconv64 executable.

    tools_path may be:
      - full path to audioconv64(.exe)
      - directory containing audioconv64(.exe)
      - libdragon N64_INST root (bin/audioconv64.exe is used)
    """
    def try_file(p: Path) -> Path | None:
        q = p.expanduser()
        if _exe_ok(q):
            return q.resolve()
        return None

    env_direct = os.environ.get("AUDIOCONV64", "").strip()
    if env_direct:
        hit = try_file(Path(env_direct))
        if hit:
            return hit

    if tools_path is not None:
        tp = tools_path.expanduser()
        try:
            tp = tp.resolve()
        except OSError:
            tp = tp
        hit = try_file(tp)
        if hit and hit.name.lower().startswith("audioconv64"):
            return hit
        for name in _names():
            hit = try_file(tp / name)
            if hit:
                return hit
        b = tp / "bin"
        if b.is_dir():
            for name in _names():
                hit = try_file(b / name)
                if hit:
                    return hit

    n64 = os.environ.get("N64_INST", "").strip()
    if n64:
        root = Path(n64).expanduser()
        for name in _names():
            hit = try_file(root / "bin" / name)
            if hit:
                return hit

    here = Path(__file__).resolve().parent
    for name in _names():
        hit = try_file(here / name)
        if hit:
            return hit

    w = shutil.which("audioconv64")
    if w:
        hit = try_file(Path(w))
        if hit:
            return hit

    home = Path.home()
    for name in _names():
        hit = try_file(home / "libdragon-n64-inst" / "bin" / name)
        if hit:
            return hit
    if not _is_win():
        hit = try_file(Path("/opt/libdragon/bin/audioconv64"))
        if hit:
            return hit
    return None


def run_audioconv64(
    audioconv: Path,
    out_dir: Path,
    inputs: list[Path],
    *,
    verbose: bool = True,
    extra_args: list[str] | None = None,
    log: LogFn = print,
    cancel: CancelFn = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for inp in inputs:
        inp = inp.expanduser().resolve()
        if not inp.is_file():
            raise Wav64ConversionError(f"not a file: {inp}")
        ext = inp.suffix.lower()
        if ext not in (".wav", ".aiff", ".aif", ".mp3"):
            raise Wav64ConversionError(
                f"unsupported type {ext!r}: {inp.name} (use .wav / .aiff / .mp3)"
            )

    for inp in inputs:
        if cancel and cancel():
            raise Wav64ConversionError("Cancelled.")
        cmd = [
            str(audioconv),
            "-o",
            _audioconv_path_arg(out_dir),
        ]
        if verbose:
            cmd.append("--verbose")
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(_audioconv_path_arg(inp))
        log("  " + " ".join(cmd))
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
                raise Wav64ConversionError("Cancelled.")
            line = line.rstrip()
            if line:
                log("  | " + line)
        proc.wait()
        if proc.returncode != 0:
            raise Wav64ConversionError(
                f"audioconv64 failed on {inp.name}, exit {proc.returncode}"
            )


def convert_many_wav64(
    inputs: list[Path],
    out_dir: Path,
    *,
    tools_path: Path | None = None,
    extra_audioconv_args: list[str] | None = None,
    verbose: bool = True,
    log: LogFn = print,
    cancel: CancelFn = None,
) -> None:
    ac = find_audioconv64(tools_path)
    if not ac:
        raise Wav64ConversionError(
            "audioconv64 not found. Set AUDIOCONV64 to the full path to the .exe, "
            "or choose the folder that contains audioconv64.exe, "
            "or install libdragon and set N64_INST, "
            "or add audioconv64 to PATH."
        )
    out_dir = out_dir.resolve()
    log(f"Output directory: {out_dir}")
    log(f"audioconv64: {ac}")
    log("")

    for i, inp in enumerate(inputs):
        inp = inp.expanduser().resolve()
        if not inp.is_file():
            raise Wav64ConversionError(f"not a file: {inp}")
        log(f"[{i + 1}/{len(inputs)}] {inp.name}")
        run_audioconv64(
            ac,
            out_dir,
            [inp],
            verbose=verbose,
            extra_args=extra_audioconv_args,
            log=log,
            cancel=cancel,
        )
        stem = inp.stem
        w64 = out_dir / f"{stem}.wav64"
        if w64.is_file():
            log(f"  -> {w64}")
        log("")
