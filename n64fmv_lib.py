"""
Shared conversion logic for N64 FMV (.m1v + .wav64). Used by video2n64 CLI and GUI.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import json
import math
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path


class ConversionError(Exception):
    pass


LogFn = Callable[[str], None]
CancelFn = Callable[[], bool] | None

SD_PRELOAD_MAX_BYTES = 8 * 1024 * 1024
SD_PRELOAD_TARGET_BYTES = int(SD_PRELOAD_MAX_BYTES * 0.85)
SD_V2_PRELOAD_TARGET_BYTES = int((4 * 1024 * 1024) * 0.85)


def which_ffmpeg() -> str:
    p = shutil.which("ffmpeg")
    if not p:
        raise ConversionError("ffmpeg not found in PATH. Install ffmpeg and try again.")
    return p


def which_ffprobe() -> str | None:
    return shutil.which("ffprobe")


def find_audioconv64(n64_inst: Path | None) -> Path | None:
    def _is_runnable(p: Path) -> bool:
        try:
            return p.is_file() and p.stat().st_size > 0 and os.access(p, os.X_OK)
        except OSError:
            return False

    def _from_prefix(prefix: Path) -> Path | None:
        candidates = (
            prefix / "bin" / "audioconv64",
            prefix / "audioconv64",
            prefix / "tools" / "audioconv64",
            prefix / "tools" / "audioconv64" / "audioconv64",
        )
        for c in candidates:
            if _is_runnable(c):
                return c
        return None

    if n64_inst:
        p = _from_prefix(n64_inst)
        if p:
            return p
        # Fall through to global discovery if a provided prefix is invalid.
    env = os.environ.get("N64_INST", "").strip()
    if env:
        p = _from_prefix(Path(env))
        if p:
            return p
    home = Path.home()
    for candidate in (
        home / "libdragon-n64-inst" / "bin" / "audioconv64",
        home / "Projects" / "libdragon-n64-inst" / "bin" / "audioconv64",
        home / "Projects" / "libdragon-preview" / "tools" / "audioconv64",
        home / "Projects" / "libdragon-preview" / "tools" / "audioconv64" / "audioconv64",
        home / "libdragon-preview" / "tools" / "audioconv64",
        home / "libdragon-preview" / "tools" / "audioconv64" / "audioconv64",
        Path("/opt/libdragon/bin/audioconv64"),
    ):
        if _is_runnable(candidate):
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


def parse_bitrate_kbps(rate: str) -> int | None:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kKmM]?)\s*", rate)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2).lower()
    factor = 1000 if unit == "m" else 1
    kbps = int(value * factor)
    if kbps <= 0:
        return None
    return kbps


def bitrate_arg_from_kbps(kbps: int) -> str:
    return f"{kbps}K"


def probe_duration_sec(ffprobe: str | None, input_path: Path) -> float | None:
    if not ffprobe:
        return None
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(input_path),
    ]
    try:
        out = subprocess.check_output(cmd, text=True).strip()
        if not out:
            return None
        sec = float(out)
        return sec if sec > 0 else None
    except (subprocess.SubprocessError, ValueError):
        return None


def probe_video_stream(ffprobe: str | None, input_path: Path) -> tuple[int, int, float] | None:
    if not ffprobe:
        return None
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,display_aspect_ratio",
        "-of",
        "json",
        str(input_path),
    ]
    try:
        out = subprocess.check_output(cmd, text=True)
        data = json.loads(out)
        streams = data.get("streams") or []
        if not streams:
            return None
        s = streams[0]
        w = int(s.get("width") or 0)
        h = int(s.get("height") or 0)
        if w <= 0 or h <= 0:
            return None
        dar_raw = str(s.get("display_aspect_ratio") or "").strip()
        dar = float(w) / float(h)
        if ":" in dar_raw:
            num, den = dar_raw.split(":", 1)
            try:
                n = float(num)
                d = float(den)
                if n > 0 and d > 0:
                    dar = n / d
            except ValueError:
                pass
        return (w, h, dar)
    except (subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return None


def duration_to_text(sec: float) -> str:
    total = int(sec + 0.5)
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def auto_tune_limits(duration_sec: float) -> tuple[int, int, int] | None:
    # Conservative caps to reduce decode spikes on real hardware for long content.
    if duration_sec >= 1200:
        return (320, 24, 16000)
    if duration_sec >= 900:
        return (360, 24, 16000)
    if duration_sec >= 600:
        return (420, 24, 22050)
    if duration_sec >= 300:
        return (520, 24, 22050)
    return None


def recommended_scale(dar: float, duration_sec: float | None) -> str | None:
    is_wide = dar >= 1.60
    if duration_sec is not None and duration_sec >= 1200:
        return "224:128" if is_wide else "224:160"
    if duration_sec is not None and duration_sec >= 600:
        return "288:160" if is_wide else "288:208"
    return None


def legalize_mpeg1_fps(requested_fps: int) -> tuple[str, bool]:
    legal_int = {24, 25, 30, 50, 60}
    if requested_fps in legal_int:
        return (str(requested_fps), False)
    # ffmpeg/mpeg1video accepts only MPEG-standard rates; pick nearest practical rate.
    if requested_fps <= 24:
        return ("24000/1001", True)
    if requested_fps <= 30:
        return ("30000/1001", True)
    if requested_fps <= 60:
        return ("60000/1001", True)
    return ("60", True)


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
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as e:
        raise ConversionError(
            "Could not execute audioconv64 "
            f"({audioconv}): {e}. "
            "Your toolchain binary may be invalid; try libdragon-preview/tools/audioconv64."
        ) from e
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
    fps: int = 24
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
    auto_tune_long_videos: bool = True
    force_cbr: bool = True
    chunk_seconds: float | None = None
    chunk_auto: bool = False
    fit_sd_preload: bool = True
    sd_profile: str = "default"


def _stream_subprocess(cmd: list[str], *, cwd: Path, env: dict[str, str], log: LogFn, cancel: CancelFn = None) -> None:
    log("  " + " ".join(cmd))
    if cancel and cancel():
        raise ConversionError("Cancelled.")
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
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
        raise ConversionError(f"command failed with exit {proc.returncode}: {' '.join(cmd)}")


def _copy_if_exists(src: Path, dst: Path) -> None:
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _has_n64_mk(prefix: Path | None) -> bool:
    if not prefix:
        return False
    try:
        return (prefix / "include" / "n64.mk").is_file()
    except OSError:
        return False


def _resolve_build_n64_inst(preferred: Path | None) -> Path | None:
    candidates: list[Path] = []
    if preferred:
        candidates.append(preferred)
        # Common case: user points to libdragon-preview but install prefix exists nearby.
        if preferred.name == "libdragon-preview":
            candidates.append(preferred.parent / "libdragon-n64-inst")
    env = os.environ.get("N64_INST", "").strip()
    if env:
        envp = Path(env).expanduser()
        candidates.append(envp)
        if envp.name == "libdragon-preview":
            candidates.append(envp.parent / "libdragon-n64-inst")
    home = Path.home()
    candidates.extend(
        [
            home / "Projects" / "libdragon-n64-inst",
            home / "libdragon-n64-inst",
            Path("/opt/libdragon"),
        ]
    )
    seen: set[Path] = set()
    for c in candidates:
        c = c.expanduser()
        if c in seen:
            continue
        seen.add(c)
        if _has_n64_mk(c):
            return c
    return None


def _audio_kbps_estimate(audio_hz: int, with_audio: bool) -> int:
    if not with_audio:
        return 0
    if audio_hz >= 32000:
        return 72
    if audio_hz >= 24000:
        return 56
    return 40


def _clamp_to_min(v: int, min_v: int) -> int:
    return v if v >= min_v else min_v


def _rom_fit_profiles(base: ConvertOptions, duration_sec: float | None) -> list[ConvertOptions]:
    # Best quality first, then progressively safer profiles.
    base_kbps = parse_bitrate_kbps(base.video_bitrate) or 800
    fps = base.fps
    audio_hz = base.audio_hz
    scale = base.scale

    if base.auto_tune_long_videos and duration_sec and duration_sec > 900:
        # Long content starts with safer values, still best-first within limits.
        base_kbps = min(base_kbps, 520)
        fps = min(fps, 24)
        audio_hz = min(audio_hz, 24000)
        if scale == "320:-16":
            scale = "288:160"

    profiles = [
        (scale, base_kbps, fps, audio_hz),
        (scale, int(base_kbps * 0.85), min(fps, 24), audio_hz),
        (scale, int(base_kbps * 0.72), min(fps, 24), min(audio_hz, 24000)),
        ("288:160", min(520, int(base_kbps * 0.75)), min(fps, 24), min(audio_hz, 24000)),
        ("256:144", 420, 24, 24000),
        ("224:128", 340, 24, 22050),
        ("192:112", 260, 24, 22050),
    ]

    out: list[ConvertOptions] = []
    seen: set[tuple[str, int, int, int]] = set()
    for p_scale, p_kbps, p_fps, p_hz in profiles:
        p_kbps = _clamp_to_min(p_kbps, 180)
        p_fps = _clamp_to_min(min(p_fps, 60), 20)
        key = (p_scale, p_kbps, p_fps, p_hz)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            replace(
                base,
                scale=p_scale,
                video_bitrate=bitrate_arg_from_kbps(p_kbps),
                fps=p_fps,
                audio_hz=p_hz,
                chunk_seconds=None,
                chunk_auto=False,
            )
        )
    return out


def _save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(text)


def convert_to_single_rom_fit(
    input_path: Path,
    out_dir: Path,
    *,
    repo_root: Path,
    n64_inst: Path | None,
    stem_override: str | None,
    opts: ConvertOptions,
    max_rom_mb: float = 64.0,
    rom_filename: str = "n64video.z64",
    log: LogFn = print,
    cancel: CancelFn = None,
) -> None:
    """
    Build one embedded-video ROM that fits within max size.
    Tries best quality first and degrades only as needed.
    """
    input_path = input_path.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()
    repo_root = repo_root.expanduser().resolve()
    fs_dir = repo_root / "filesystem"
    if not input_path.is_file():
        raise ConversionError(f"not a file: {input_path}")
    if not (repo_root / "Makefile").is_file():
        raise ConversionError(f"repo root missing Makefile: {repo_root}")
    if not fs_dir.is_dir():
        raise ConversionError(f"repo root missing filesystem dir: {fs_dir}")
    if max_rom_mb <= 1.0:
        raise ConversionError("max ROM size must be > 1 MB")

    ffmpeg = which_ffmpeg()
    ffprobe = which_ffprobe()
    audioconv = None if opts.skip_wav64 or opts.no_audio else find_audioconv64(n64_inst)
    build_n64_inst = _resolve_build_n64_inst(n64_inst)
    if not build_n64_inst:
        raise ConversionError(
            "Could not find a buildable libdragon install prefix (missing include/n64.mk). "
            "Set --n64-inst to your install (usually ~/Projects/libdragon-n64-inst)."
        )

    duration_sec = opts.duration_sec
    if duration_sec is None:
        duration_sec = probe_duration_sec(ffprobe, input_path)

    # Quick feasibility warning based on conservative bitrate math.
    max_bytes = int(max_rom_mb * 1024 * 1024)
    if duration_sec and duration_sec > 0:
        overhead = 1200 * 1024  # ELF + DFS/container slack
        payload = max(max_bytes - overhead, 0)
        audio_kbps = _audio_kbps_estimate(opts.audio_hz, not opts.no_audio)
        max_video_kbps = int((payload * 8.0 / duration_sec) / 1000.0) - audio_kbps
        log(
            f"ROM budget: {max_rom_mb:.1f} MB, duration {duration_to_text(duration_sec)}, "
            f"rough max video bitrate {max_video_kbps}K"
        )
        if max_video_kbps < 170:
            raise ConversionError(
                f"Video is too long for a {max_rom_mb:.1f}MB ROM even at very low quality "
                f"(rough max video bitrate {max_video_kbps}K)."
            )

    stem = stem_override if stem_override else stem_from_input(input_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_assets = out_dir / "_fit_tmp"
    tmp_assets.mkdir(parents=True, exist_ok=True)

    profiles = _rom_fit_profiles(replace(opts, chunk_auto=False, chunk_seconds=None), duration_sec)
    log(f"Trying {len(profiles)} quality profile(s) to fit <= {max_rom_mb:.1f} MB")

    backups = {
        "movie.m1v": fs_dir / "movie.m1v",
        "movie.wav64": fs_dir / "movie.wav64",
        "movie.wav": fs_dir / "movie.wav",
    }
    best_size = 1 << 60
    best_desc = "(none)"
    chosen_desc = ""
    with tempfile.TemporaryDirectory(prefix="n64romfit_backup_") as tdir:
        backup_dir = Path(tdir)
        for name, path in backups.items():
            _copy_if_exists(path, backup_dir / name)
        try:
            for idx, p in enumerate(profiles, start=1):
                if cancel and cancel():
                    raise ConversionError("Cancelled.")
                # Cleanup temporary outputs from prior attempt.
                for ext in (".m1v", ".wav", ".wav64"):
                    try:
                        (tmp_assets / f"{stem}{ext}").unlink()
                    except FileNotFoundError:
                        pass
                desc = f"scale={p.scale} bitrate={p.video_bitrate} fps={p.fps} audio_hz={p.audio_hz}"
                log(f"[fit {idx}/{len(profiles)}] {desc}")
                convert_one(
                    input_path,
                    tmp_assets,
                    stem,
                    ffmpeg=ffmpeg,
                    ffprobe=ffprobe,
                    audioconv=audioconv,
                    opts=p,
                    log=log,
                    cancel=cancel,
                )

                src_m1v = tmp_assets / f"{stem}.m1v"
                src_w64 = tmp_assets / f"{stem}.wav64"
                if not src_m1v.is_file():
                    raise ConversionError("conversion did not produce .m1v")
                shutil.copy2(src_m1v, fs_dir / "movie.m1v")
                if src_w64.is_file():
                    shutil.copy2(src_w64, fs_dir / "movie.wav64")
                else:
                    try:
                        (fs_dir / "movie.wav64").unlink()
                    except FileNotFoundError:
                        pass

                for stale in (repo_root / "build" / "n64video.dfs", repo_root / "n64video.z64"):
                    try:
                        stale.unlink()
                    except FileNotFoundError:
                        pass

                env = os.environ.copy()
                env["N64_INST"] = str(build_n64_inst)
                _stream_subprocess(
                    ["make", "-j4", "n64video.z64"],
                    cwd=repo_root,
                    env=env,
                    log=log,
                    cancel=cancel,
                )
                built = repo_root / "n64video.z64"
                if not built.is_file():
                    raise ConversionError("build did not produce n64video.z64")
                size = built.stat().st_size
                if size < best_size:
                    best_size = size
                    best_desc = desc
                log(f"  -> ROM size: {size / (1024 * 1024):.2f} MB")
                if size <= max_bytes:
                    final_rom = out_dir / rom_filename
                    shutil.copy2(built, final_rom)
                    chosen_desc = desc
                    _save_text(
                        out_dir / "ROM_FIT_REPORT.txt",
                        (
                            "ROM fit success\n"
                            f"source={input_path}\n"
                            f"profile={desc}\n"
                            f"size_bytes={size}\n"
                            f"size_mb={size / (1024 * 1024):.3f}\n"
                            f"max_mb={max_rom_mb:.3f}\n"
                        ),
                    )
                    log(f"Fit success: {final_rom}")
                    break
            if not chosen_desc:
                raise ConversionError(
                    f"Could not fit into {max_rom_mb:.1f}MB ROM after {len(profiles)} attempts. "
                    f"Smallest build was {best_size / (1024 * 1024):.2f}MB ({best_desc})."
                )
        finally:
            for name, path in backups.items():
                b = backup_dir / name
                if b.is_file():
                    shutil.copy2(b, path)
                else:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
    shutil.rmtree(tmp_assets, ignore_errors=True)


def convert_to_menu_rom_bundle(
    input_path: Path,
    out_dir: Path,
    *,
    repo_root: Path,
    n64_inst: Path | None,
    stem_override: str | None,
    opts: ConvertOptions,
    build_engine_rom: bool = True,
    log: LogFn = print,
    cancel: CancelFn = None,
) -> None:
    """
    User-friendly default mode:
    - Convert one input into a single .m1v/.wav64 pair for sdvideo.z64.
    - Optionally build sdvideo.z64 and place it in ENGINES/SDVIDEO.Z64.
    - Emit a starter VIDEO.CFG template for one-click autoplay handoff.
    """
    input_path = input_path.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()
    repo_root = repo_root.expanduser().resolve()
    if not input_path.is_file():
        raise ConversionError(f"not a file: {input_path}")
    if not (repo_root / "Makefile.sd").is_file():
        raise ConversionError(f"repo root missing Makefile.sd: {repo_root}")

    stem = stem_override if stem_override else stem_from_input(input_path)
    videos_dir = out_dir / "videos"
    engines_dir = out_dir / "ENGINES"
    videos_dir.mkdir(parents=True, exist_ok=True)
    engines_dir.mkdir(parents=True, exist_ok=True)

    bundle_opts = replace(opts, chunk_seconds=None, chunk_auto=False)
    log("Menu ROM mode: using single video/audio asset pair (no chunking).")

    log(f"Menu ROM bundle output: {out_dir}")
    log(f"Repo root: {repo_root}")
    log("")

    convert_many(
        [input_path],
        videos_dir,
        n64_inst=n64_inst,
        stem_override=stem,
        opts=bundle_opts,
        log=log,
        cancel=cancel,
    )

    first = videos_dir / f"{stem}.m1v"
    if not first.is_file():
        raise ConversionError("conversion produced no playable .m1v output")

    if build_engine_rom:
        build_n64_inst = _resolve_build_n64_inst(n64_inst)
        if not build_n64_inst:
            raise ConversionError(
                "Could not find a buildable libdragon install prefix (missing include/n64.mk). "
                "Set --n64-inst to your install (usually ~/Projects/libdragon-n64-inst)."
            )
        log(f"Building SDVIDEO.Z64 with toolchain: {build_n64_inst}")
        env = os.environ.copy()
        env["N64_INST"] = str(build_n64_inst)
        _stream_subprocess(
            ["make", "-f", "Makefile.sd", "-j4", "sdvideo.z64"],
            cwd=repo_root,
            env=env,
            log=log,
            cancel=cancel,
        )
        built = repo_root / "sdvideo.z64"
        if not built.is_file():
            raise ConversionError(f"build did not produce {built}")
        shutil.copy2(built, engines_dir / "SDVIDEO.Z64")

    rel_first = first.relative_to(out_dir).as_posix()
    cfg = out_dir / "VIDEO.CFG.example"
    with cfg.open("w", encoding="utf-8") as f:
        f.write("# One-shot autoplay handoff for sdvideo.z64\n")
        f.write("# Copy to: /ED64P/VIDEO.CFG (or /ED64/VIDEO.CFG)\n")
        f.write(f"video=sd:/{rel_first}\n")
        f.write("mode=smooth\n")
        f.write(f"browser_dir=sd:/{videos_dir.relative_to(out_dir).as_posix()}/\n")
        f.write(f"browser_name={first.name}\n")

    guide = out_dir / "README_MENU_ROM.txt"
    with guide.open("w", encoding="utf-8") as f:
        f.write("Menu ROM seamless playback bundle\n")
        f.write("================================\n\n")
        f.write("1) Copy ENGINES/SDVIDEO.Z64 to your cart firmware ENGINES folder.\n")
        f.write("2) Copy videos/*.m1v and videos/*.wav64 anywhere on SD (keep names).\n")
        f.write("3) Boot SDVIDEO.Z64 and select your single .m1v file.\n")
        f.write("4) Optional autoplay: copy VIDEO.CFG.example to /ED64P/VIDEO.CFG.\n")

    log("")
    log(f"Seamless videos: {videos_dir}")
    if build_engine_rom:
        log(f"Engine ROM: {engines_dir / 'SDVIDEO.Z64'}")
    log(f"Autoplay template: {cfg}")
    log(f"Guide: {guide}")


def convert_to_chunk_rom_pack(
    input_path: Path,
    out_dir: Path,
    *,
    repo_root: Path,
    n64_inst: Path | None,
    stem_override: str | None,
    opts: ConvertOptions,
    log: LogFn = print,
    cancel: CancelFn = None,
) -> None:
    input_path = input_path.expanduser().resolve()
    repo_root = repo_root.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()
    fs_dir = repo_root / "filesystem"
    makefile = repo_root / "Makefile"
    if not input_path.is_file():
        raise ConversionError(f"not a file: {input_path}")
    if not makefile.is_file():
        raise ConversionError(f"repo root missing Makefile: {repo_root}")
    if not fs_dir.is_dir():
        raise ConversionError(f"repo root missing filesystem dir: {fs_dir}")

    stem = stem_override if stem_override else stem_from_input(input_path)
    assets_dir = out_dir / "assets"
    roms_dir = out_dir / "roms"
    assets_dir.mkdir(parents=True, exist_ok=True)
    roms_dir.mkdir(parents=True, exist_ok=True)

    chunk_opts = opts
    if not chunk_opts.chunk_seconds and not chunk_opts.chunk_auto:
        # Default ROM-pack mode to auto chunking.
        chunk_opts = replace(chunk_opts, chunk_auto=True)
        log("ROM pack mode: enabling auto chunk sizing.")

    log(f"ROM pack output: {out_dir}")
    log(f"Repo root: {repo_root}")
    log("")

    convert_many(
        [input_path],
        assets_dir,
        n64_inst=n64_inst,
        stem_override=stem,
        opts=chunk_opts,
        log=log,
        cancel=cancel,
    )

    stems: list[str] = []
    part_files = sorted(assets_dir.glob(f"{stem}_part*.m1v"))
    if part_files:
        stems = [p.stem for p in part_files]
    else:
        single = assets_dir / f"{stem}.m1v"
        if not single.is_file():
            raise ConversionError(f"conversion produced no m1v assets under {assets_dir}")
        stems = [stem]

    manifest = out_dir / "playlist.txt"
    with manifest.open("w", encoding="utf-8") as f:
        f.write("# Sequential ROM playback order\n")
        for s in stems:
            f.write(f"{s}.z64\n")

    log(f"Building {len(stems)} ROM chunk(s)…")
    build_n64_inst = _resolve_build_n64_inst(n64_inst)
    if not build_n64_inst:
        raise ConversionError(
            "Could not find a buildable libdragon install prefix (missing include/n64.mk). "
            "Set --n64-inst to your install (usually ~/Projects/libdragon-n64-inst)."
        )
    log(f"ROM build toolchain: {build_n64_inst}")

    backups = {
        "movie.m1v": fs_dir / "movie.m1v",
        "movie.wav64": fs_dir / "movie.wav64",
        "movie.wav": fs_dir / "movie.wav",
    }
    with tempfile.TemporaryDirectory(prefix="n64rompack_backup_") as tdir:
        backup_dir = Path(tdir)
        for name, path in backups.items():
            _copy_if_exists(path, backup_dir / name)

        try:
            for idx, s in enumerate(stems, start=1):
                if cancel and cancel():
                    raise ConversionError("Cancelled.")
                src_m1v = assets_dir / f"{s}.m1v"
                src_w64 = assets_dir / f"{s}.wav64"
                if not src_m1v.is_file():
                    raise ConversionError(f"missing chunk asset: {src_m1v}")

                shutil.copy2(src_m1v, fs_dir / "movie.m1v")
                if src_w64.is_file():
                    shutil.copy2(src_w64, fs_dir / "movie.wav64")
                else:
                    try:
                        (fs_dir / "movie.wav64").unlink()
                    except FileNotFoundError:
                        pass

                log(f"[rom {idx:03d}/{len(stems):03d}] {s}.z64")
                env = os.environ.copy()
                env["N64_INST"] = str(build_n64_inst)
                _stream_subprocess(
                    ["make", "-j4", "n64video.z64"],
                    cwd=repo_root,
                    env=env,
                    log=log,
                    cancel=cancel,
                )
                built = repo_root / "n64video.z64"
                if not built.is_file():
                    raise ConversionError(f"build did not produce {built}")
                shutil.copy2(built, roms_dir / f"{s}.z64")
        finally:
            # Always restore user workspace assets.
            for name, path in backups.items():
                b = backup_dir / name
                if b.is_file():
                    shutil.copy2(b, path)
                else:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass

    log("")
    log(f"ROM chunks: {roms_dir}")
    log(f"Assets: {assets_dir}")
    log(f"Playlist: {manifest}")


def choose_auto_chunk_seconds(
    duration_sec: float,
    video_bitrate_kbps: int | None,
    audio_hz: int,
    scale: str,
) -> float:
    # Approximate effective audio payload with VADPCM overhead.
    if audio_hz >= 32000:
        audio_kbps = 72
    elif audio_hz >= 24000:
        audio_kbps = 56
    else:
        audio_kbps = 40
    v_kbps = video_bitrate_kbps if video_bitrate_kbps else 420
    total_kbps = v_kbps + audio_kbps

    # Target chunk payload around ~10 MiB to reduce long-stream stress
    # while keeping transitions infrequent.
    target_mib = 10.0
    by_size = (target_mib * 8192.0) / max(total_kbps, 1)

    # Heavy profiles get shorter chunks.
    width = 320
    try:
        width = int((scale or "320:-16").split(":")[0])
    except (ValueError, IndexError):
        width = 320
    if width >= 320 or v_kbps >= 600:
        cap = 180.0
    elif v_kbps >= 450:
        cap = 240.0
    else:
        cap = 300.0

    # For short videos, avoid needless chunking.
    if duration_sec <= 240:
        return duration_sec

    sec = min(by_size, cap)
    if sec < 120.0:
        sec = 120.0
    return sec


def clamp_chunk_for_sd_preload(
    chunk_seconds: float,
    video_bitrate_kbps: int | None,
    target_bytes: int = SD_PRELOAD_TARGET_BYTES,
) -> float:
    if chunk_seconds <= 0:
        return chunk_seconds
    v_kbps = video_bitrate_kbps if video_bitrate_kbps and video_bitrate_kbps > 0 else 420
    max_sec = (target_bytes * 8.0) / (v_kbps * 1000.0)
    if max_sec < 20.0:
        max_sec = 20.0
    return min(chunk_seconds, max_sec)


def convert_one(
    input_path: Path,
    out_dir: Path,
    stem: str,
    *,
    ffmpeg: str,
    ffprobe: str | None,
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
    effective_fps = opts.fps
    effective_audio_hz = opts.audio_hz
    effective_bitrate = opts.video_bitrate
    effective_bitrate_kbps = parse_bitrate_kbps(opts.video_bitrate)
    effective_scale = opts.scale
    video_probe = probe_video_stream(ffprobe, input_path)
    if video_probe:
        w, h, dar = video_probe
        log(f"  [probe] video stream: {w}x{h}, DAR={dar:.3f}")
    else:
        dar = 16.0 / 9.0

    duration_sec = opts.duration_sec
    if duration_sec is None:
        duration_sec = probe_duration_sec(ffprobe, input_path)
        if duration_sec is not None:
            log(f"  [probe] input duration: {duration_to_text(duration_sec)}")

    if (
        opts.auto_tune_long_videos
        and duration_sec is not None
        and effective_bitrate_kbps is not None
    ):
        limits = auto_tune_limits(duration_sec)
        if limits:
            max_kbps, max_fps, max_audio_hz = limits
            tuned = False
            if effective_bitrate_kbps > max_kbps:
                effective_bitrate_kbps = max_kbps
                effective_bitrate = bitrate_arg_from_kbps(max_kbps)
                tuned = True
            if effective_fps > max_fps:
                effective_fps = max_fps
                tuned = True
            if effective_fps < 24:
                effective_fps = 24
                tuned = True
            if effective_audio_hz > max_audio_hz:
                effective_audio_hz = max_audio_hz
                tuned = True
            if opts.scale == "320:-16":
                rec_scale = recommended_scale(dar, duration_sec)
                if rec_scale and rec_scale != effective_scale:
                    effective_scale = rec_scale
                    tuned = True
            if tuned:
                log(
                    "  [auto-tune] long video profile "
                    f"({duration_to_text(duration_sec)}): "
                    f"{effective_bitrate}, {effective_fps} fps, "
                    f"{effective_audio_hz} Hz, scale={effective_scale}"
                )

    fps_arg, fps_adjusted = legalize_mpeg1_fps(effective_fps)
    if fps_adjusted:
        log(
            f"  [fps] requested {effective_fps} not MPEG-1 standard; using {fps_arg}"
        )

    vf = build_vf(effective_scale, opts.vf_extra or None)
    video_args = [
        *tflags,
        "-i",
        str(input_path),
        "-c:v",
        "mpeg1video",
        "-vb",
        effective_bitrate,
    ]
    if opts.force_cbr:
        cbr_maxrate = effective_bitrate
        cbr_bufsize = None
        if effective_bitrate_kbps is not None:
            cbr_bufsize = bitrate_arg_from_kbps(effective_bitrate_kbps)
        video_args.extend(["-minrate", effective_bitrate, "-maxrate", cbr_maxrate])
        if cbr_bufsize:
            video_args.extend(["-bufsize", cbr_bufsize])
            log(
                f"  [ratectl] cbr-ish mpeg1 min/max={effective_bitrate}, buf={cbr_bufsize}"
            )
        else:
            log(f"  [ratectl] cbr-ish mpeg1 min/max={effective_bitrate}")
    # Decoder-friendly stream shape on N64: no B-frames, short GOP.
    video_args.extend(["-bf", "0", "-g", "12", "-vf", vf, "-r", fps_arg, str(m1v)])

    run_ffmpeg(
        ffmpeg,
        video_args,
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
            str(effective_audio_hz),
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
    ffprobe = which_ffprobe()
    audioconv = (
        None
        if opts.skip_wav64 or opts.no_audio
        else find_audioconv64(n64_inst)
    )
    out_dir = out_dir.resolve()
    log(f"Output directory: {out_dir}")
    if ffprobe:
        log(f"ffprobe: {ffprobe}")
    else:
        log("ffprobe: (not found — input-duration auto-tuning may be limited)")
    if audioconv:
        log(f"audioconv64: {audioconv}")
    else:
        log("audioconv64: (not found — .wav64 skipped unless you set toolchain path)")
    log("")

    sd_profile = (opts.sd_profile or "default").strip().lower()
    if sd_profile not in {"default", "v2"}:
        raise ConversionError("sd_profile must be 'default' or 'v2'")
    sd_target_bytes = (
        SD_V2_PRELOAD_TARGET_BYTES if sd_profile == "v2" else SD_PRELOAD_TARGET_BYTES
    )
    log(
        f"SD profile: {sd_profile} "
        f"(chunk preload target {sd_target_bytes // 1024} KiB)"
    )
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
        chunk_seconds = opts.chunk_seconds
        src_v_kbps = parse_bitrate_kbps(opts.video_bitrate)
        if not chunk_seconds and opts.chunk_auto:
            total = probe_duration_sec(ffprobe, inp)
            if total is None:
                raise ConversionError("Could not probe video duration for auto chunking.")
            chunk_seconds = choose_auto_chunk_seconds(
                total,
                src_v_kbps,
                opts.audio_hz,
                opts.scale,
            )
            if opts.fit_sd_preload and chunk_seconds:
                clamped = clamp_chunk_for_sd_preload(
                    chunk_seconds, src_v_kbps, sd_target_bytes
                )
                if clamped + 0.01 < chunk_seconds:
                    log(
                        f"  [chunk-auto] clamped to {clamped:.1f}s "
                        "to fit SD preload path"
                    )
                chunk_seconds = clamped
            if chunk_seconds >= total:
                chunk_seconds = None
            else:
                log(
                    f"  [chunk-auto] selected {chunk_seconds:.1f}s chunks "
                    f"for {duration_to_text(total)} source"
                )
        elif chunk_seconds and opts.fit_sd_preload:
            clamped = clamp_chunk_for_sd_preload(
                chunk_seconds, src_v_kbps, sd_target_bytes
            )
            if clamped + 0.01 < chunk_seconds:
                log(
                    f"  [chunk] clamped from {chunk_seconds:.1f}s "
                    f"to {clamped:.1f}s for SD preload fit"
                )
            chunk_seconds = clamped

        if not chunk_seconds and sd_profile == "v2":
            total = probe_duration_sec(ffprobe, inp)
            if total and total > 240:
                chunk_seconds = clamp_chunk_for_sd_preload(
                    180.0, src_v_kbps, sd_target_bytes
                )
                if chunk_seconds >= total:
                    chunk_seconds = None
                else:
                    log(
                        "  [v2-safe] enabling chunking for long clip: "
                        f"{chunk_seconds:.1f}s parts"
                    )

        if chunk_seconds and chunk_seconds > 0:
            total = probe_duration_sec(ffprobe, inp)
            if total is None:
                raise ConversionError("Could not probe video duration for chunking.")
            base_start = opts.start_sec or 0.0
            max_dur = total - base_start
            if max_dur <= 0:
                raise ConversionError("Start time is past end of input.")
            window_dur = opts.duration_sec if opts.duration_sec else max_dur
            if window_dur > max_dur:
                window_dur = max_dur
            chunk_len = chunk_seconds
            num_chunks = int(math.ceil(window_dur / chunk_len))
            log(
                f"  [chunk] splitting into {num_chunks} parts of up to "
                f"{chunk_len:.1f}s each"
            )
            for c in range(num_chunks):
                c_start = base_start + (c * chunk_len)
                c_dur = min(chunk_len, window_dur - (c * chunk_len))
                if c_dur <= 0:
                    break
                c_stem = f"{stem}_part{(c + 1):03d}"
                c_opts = replace(
                    opts,
                    start_sec=c_start,
                    duration_sec=c_dur,
                    chunk_seconds=None,
                )
                log(
                    f"  [chunk {c + 1:03d}] start={c_start:.2f}s "
                    f"dur={c_dur:.2f}s -> {c_stem}"
                )
                convert_one(
                    inp,
                    out_dir,
                    c_stem,
                    ffmpeg=ffmpeg,
                    ffprobe=ffprobe,
                    audioconv=audioconv,
                    opts=c_opts,
                    log=log,
                    cancel=cancel,
                )
        else:
            convert_one(
                inp,
                out_dir,
                stem,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                audioconv=audioconv,
                opts=opts,
                log=log,
                cancel=cancel,
            )
        log("")
