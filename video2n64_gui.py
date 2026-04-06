#!/usr/bin/env python3
"""
GUI for converting videos to N64 FMV (.m1v + .wav64). Uses tkinter (stdlib).

Run from repo:  python3 scripts/video2n64_gui.py
Or:            cd scripts && ./video2n64_gui.py
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from n64fmv_lib import (
    ConversionError,
    ConvertOptions,
    convert_to_menu_rom_bundle,
    convert_to_single_rom_fit,
    find_audioconv64,
)

# (label, scale, bitrate, fps, unused) — None = custom only
PRESET_ENTRIES: list[tuple[str, tuple[str, str, int, str] | None]] = [
    ("Ultra smooth long episode — 224x128 @ 320K", ("224:128", "320K", 24, "")),
    ("Ultra smooth long episode 4:3 — 224x160 @ 320K", ("224:160", "320K", 24, "")),
    ("Long episode (speed first) — 288x160 @ 420K", ("288:160", "420K", 24, "")),
    ("Long episode 4:3 (speed first) — 288x208 @ 420K", ("288:208", "420K", 24, "")),
    (
        "Widescreen — 320 wide, auto height (libdragon default)",
        ("320:-16", "800K", 24, ""),
    ),
    ("Widescreen faster — 288×160, higher bitrate", ("288:160", "1000K", 24, "")),
    ("4:3 — 320×240", ("320:240", "800K", 24, "")),
    ("4:3 faster — 288×208", ("288:208", "1000K", 24, "")),
    ("Quality — 320 wide, 1200K, 24 fps (heavier on N64)", ("320:-16", "1200K", 24, "")),
    ("Low bandwidth — 600K, 24 fps", ("320:-16", "600K", 24, "")),
    ("Custom (edit fields below)", None),
]
PRESET_NAMES = [x[0] for x in PRESET_ENTRIES]
PRESET_MAP = dict(PRESET_ENTRIES)
MODE_V2 = "Single embedded ROM (EverDrive V2)"
MODE_X7 = "Video player menu + SD files (EverDrive X7/others)"
OUTPUT_MODES = (MODE_X7, MODE_V2)


def _prefix_from_audioconv_path(ac: Path) -> Path:
    # Typical install: <prefix>/bin/audioconv64
    if ac.parent.name == "bin":
        return ac.parent.parent
    # Local built tools: <prefix>/tools/audioconv64/audioconv64
    if ac.parent.name == "audioconv64" and ac.parent.parent.name == "tools":
        return ac.parent.parent.parent
    return ac.parent


def _normalize_n64_inst_input(raw: str) -> Path | None:
    s = (raw or "").strip()
    if not s:
        return None
    p = Path(s).expanduser()
    # If user selected the executable directly.
    if p.is_file() and p.name == "audioconv64":
        return _prefix_from_audioconv_path(p)
    # If user selected .../bin, promote to install prefix.
    if p.is_dir() and p.name == "bin" and (p / "audioconv64").is_file():
        return p.parent
    # If user selected .../tools/audioconv64 folder, promote to project prefix.
    if p.is_dir() and p.name == "audioconv64" and p.parent.name == "tools" and (p / "audioconv64").is_file():
        return p.parent.parent
    return p


def _guess_n64_inst() -> str:
    env = os.environ.get("N64_INST", "").strip()
    if env:
        n64p = _normalize_n64_inst_input(env)
        if n64p and (n64p / "bin" / "audioconv64").is_file():
            return str(n64p)

    ac = find_audioconv64(None)
    if ac:
        return str(_prefix_from_audioconv_path(ac))

    script = Path(__file__).resolve()
    repo_root = script.parent.parent
    candidates = [
        repo_root.parent / "libdragon-n64-inst",
        repo_root.parent / "libdragon-preview",
        Path.home() / "Projects" / "libdragon-n64-inst",
        Path.home() / "Projects" / "libdragon-preview",
        Path.home() / "libdragon-n64-inst",
        Path.home() / "libdragon-preview",
        Path("/opt/libdragon"),
    ]
    for c in candidates:
        if (c / "bin" / "audioconv64").is_file():
            return str(c)
    return ""


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("N64 FMV Converter (libdragon)")
        self.minsize(720, 560)
        self._queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None

        self._build()
        self.after(80, self._pump_queue)

    def _build(self) -> None:
        root_f = ttk.Frame(self, padding=8)
        root_f.pack(fill=tk.BOTH, expand=True)

        # --- Files ---
        f_files = ttk.LabelFrame(root_f, text="Input files", padding=6)
        f_files.pack(fill=tk.BOTH, expand=False)
        self._list = tk.Listbox(f_files, height=5, selectmode=tk.EXTENDED)
        self._list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(f_files, command=self._list.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._list.config(yscrollcommand=sb.set)
        bf = ttk.Frame(f_files)
        bf.pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(bf, text="Add files…", command=self._add_files).pack(fill=tk.X)
        ttk.Button(bf, text="Remove", command=self._remove_sel).pack(fill=tk.X, pady=4)
        ttk.Button(bf, text="Clear", command=self._clear_list).pack(fill=tk.X)

        # --- Output & toolchain ---
        f_io = ttk.Frame(root_f)
        f_io.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(f_io, text="Output folder").grid(row=0, column=0, sticky=tk.W)
        self._out = tk.StringVar(value=str(Path.cwd() / "n64-fmv-out"))
        ttk.Entry(f_io, textvariable=self._out, width=50).grid(
            row=0, column=1, sticky=tk.EW, padx=4
        )
        ttk.Button(f_io, text="Browse…", command=self._browse_out).grid(row=0, column=2)
        ttk.Label(f_io, text="N64_INST (for wav64)").grid(row=1, column=0, sticky=tk.W, pady=(4, 0))
        guessed_n64 = _guess_n64_inst()
        self._n64 = tk.StringVar(value=guessed_n64)
        ttk.Entry(f_io, textvariable=self._n64, width=50).grid(
            row=1, column=1, sticky=tk.EW, padx=4, pady=(4, 0)
        )
        ttk.Button(f_io, text="Browse…", command=self._browse_n64).grid(row=1, column=2, pady=(4, 0))
        ttk.Label(f_io, text="Repo root (for ROM/menu build)").grid(row=2, column=0, sticky=tk.W, pady=(4, 0))
        self._repo = tk.StringVar(value=str(Path(__file__).resolve().parent.parent))
        ttk.Entry(f_io, textvariable=self._repo, width=50).grid(
            row=2, column=1, sticky=tk.EW, padx=4, pady=(4, 0)
        )
        ttk.Button(f_io, text="Browse…", command=self._browse_repo).grid(row=2, column=2, pady=(4, 0))
        ttk.Label(f_io, text="Output mode").grid(row=3, column=0, sticky=tk.W, pady=(4, 0))
        self._output_mode = tk.StringVar(value=MODE_X7)
        ttk.Combobox(
            f_io,
            textvariable=self._output_mode,
            values=OUTPUT_MODES,
            state="readonly",
            width=48,
        ).grid(row=3, column=1, sticky=tk.W, padx=4, pady=(4, 0))
        f_io.columnconfigure(1, weight=1)

        # --- Encoding ---
        f_enc = ttk.LabelFrame(root_f, text="Playback / encoding (see libdragon MPEG1 wiki)", padding=6)
        f_enc.pack(fill=tk.X, pady=(8, 0))

        ttk.Label(f_enc, text="Preset").grid(row=0, column=0, sticky=tk.W)
        self._preset = tk.StringVar(value=PRESET_NAMES[0])
        cb = ttk.Combobox(
            f_enc,
            textvariable=self._preset,
            values=PRESET_NAMES,
            state="readonly",
            width=48,
        )
        cb.grid(row=0, column=1, columnspan=3, sticky=tk.EW, padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self._apply_preset())

        ttk.Label(f_enc, text="Scale (ffmpeg)").grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        self._scale = tk.StringVar(value="320:-16")
        ttk.Entry(f_enc, textvariable=self._scale, width=20).grid(
            row=1, column=1, sticky=tk.W, padx=4, pady=(6, 0)
        )
        ttk.Label(f_enc, text="Video bitrate").grid(row=1, column=2, sticky=tk.E, pady=(6, 0))
        self._vbit = tk.StringVar(value="800K")
        ttk.Entry(f_enc, textvariable=self._vbit, width=10).grid(
            row=1, column=3, sticky=tk.W, padx=4, pady=(6, 0)
        )

        ttk.Label(f_enc, text="FPS").grid(row=2, column=0, sticky=tk.W, pady=(6, 0))
        self._fps = tk.StringVar(value="24")
        ttk.Spinbox(f_enc, from_=10, to=30, textvariable=self._fps, width=8).grid(
            row=2, column=1, sticky=tk.W, padx=4, pady=(6, 0)
        )
        ttk.Label(f_enc, text="Audio Hz").grid(row=2, column=2, sticky=tk.E, pady=(6, 0))
        self._ahz = tk.StringVar(value="32000")
        ttk.Combobox(
            f_enc,
            textvariable=self._ahz,
            values=("32000", "24000", "22050", "48000"),
            width=8,
            state="readonly",
        ).grid(row=2, column=3, sticky=tk.W, padx=4, pady=(6, 0))

        ttk.Label(f_enc, text="Start (sec)").grid(row=3, column=0, sticky=tk.W, pady=(6, 0))
        self._t0 = tk.StringVar(value="")
        ttk.Entry(f_enc, textvariable=self._t0, width=12).grid(
            row=3, column=1, sticky=tk.W, padx=4, pady=(6, 0)
        )
        ttk.Label(f_enc, text="Duration (sec, empty=all)").grid(row=3, column=2, sticky=tk.E, pady=(6, 0))
        self._dur = tk.StringVar(value="")
        ttk.Entry(f_enc, textvariable=self._dur, width=12).grid(
            row=3, column=3, sticky=tk.W, padx=4, pady=(6, 0)
        )

        ttk.Label(f_enc, text="Output name (single file only)").grid(
            row=4, column=0, sticky=tk.W, pady=(6, 0)
        )
        self._stem = tk.StringVar(value="")
        ttk.Entry(f_enc, textvariable=self._stem, width=30).grid(
            row=4, column=1, columnspan=3, sticky=tk.W, padx=4, pady=(6, 0)
        )

        ttk.Label(f_enc, text="Extra ffmpeg filters (after scale)").grid(
            row=5, column=0, sticky=tk.W, pady=(6, 0)
        )
        self._vf_extra = tk.StringVar(value="")
        ttk.Entry(f_enc, textvariable=self._vf_extra, width=55).grid(
            row=5, column=1, columnspan=3, sticky=tk.EW, padx=4, pady=(6, 0)
        )

        f_chk = ttk.Frame(f_enc)
        f_chk.grid(row=6, column=0, columnspan=4, sticky=tk.W, pady=(8, 0))
        self._gamma = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            f_chk,
            text="Gamma 0.4545 (wiki: CRT / washed-out fix)",
            variable=self._gamma,
        ).pack(side=tk.LEFT)
        self._noaud = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_chk, text="Video only (.m1v)", variable=self._noaud).pack(
            side=tk.LEFT, padx=(12, 0)
        )
        self._skip64 = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_chk, text="Skip .wav64 (keep .wav)", variable=self._skip64).pack(
            side=tk.LEFT, padx=(12, 0)
        )
        self._keepwav = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_chk, text="Keep .wav after wav64", variable=self._keepwav).pack(
            side=tk.LEFT, padx=(12, 0)
        )
        self._auto_tune = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            f_chk,
            text="Auto-tune long videos (fps/scale/bitrate/audio)",
            variable=self._auto_tune,
        ).pack(side=tk.LEFT, padx=(12, 0))
        self._force_cbr = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            f_chk,
            text="Steady MPEG rate control (reduce lag spikes)",
            variable=self._force_cbr,
        ).pack(side=tk.LEFT, padx=(12, 0))

        f_enc.columnconfigure(1, weight=1)

        # --- Log & actions ---
        f_log = ttk.LabelFrame(root_f, text="Log", padding=4)
        f_log.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self._log = tk.Text(f_log, height=12, state=tk.DISABLED, wrap=tk.WORD, font=("monospace", 9))
        self._log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lsb = ttk.Scrollbar(f_log, command=self._log.yview)
        lsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._log.config(yscrollcommand=lsb.set)

        f_btn = ttk.Frame(root_f)
        f_btn.pack(fill=tk.X, pady=(8, 0))
        self._btn_go = ttk.Button(f_btn, text="Convert", command=self._start)
        self._btn_go.pack(side=tk.LEFT)
        self._btn_cancel = ttk.Button(f_btn, text="Cancel", command=self._cancel_run, state=tk.DISABLED)
        self._btn_cancel.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(
            f_btn,
            text="Lower res / bitrate = easier decoding on N64. Match .m1v and .wav64 base names.",
        ).pack(side=tk.LEFT, padx=(16, 0))

    def _apply_preset(self) -> None:
        name = self._preset.get()
        data = PRESET_MAP.get(name)
        if data is None:
            return
        scale, br, fps, _ = data
        self._scale.set(scale)
        self._vbit.set(br)
        self._fps.set(str(fps))

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Video files",
            filetypes=[
                ("Video", "*.mp4 *.mkv *.avi *.webm *.mov *.m4v"),
                ("All", "*.*"),
            ],
        )
        for p in paths:
            self._list.insert(tk.END, p)

    def _remove_sel(self) -> None:
        for i in reversed(self._list.curselection()):
            self._list.delete(i)

    def _clear_list(self) -> None:
        self._list.delete(0, tk.END)

    def _browse_out(self) -> None:
        d = filedialog.askdirectory(title="Output folder")
        if d:
            self._out.set(d)

    def _browse_n64(self) -> None:
        d = filedialog.askdirectory(title="N64_INST (folder containing bin/audioconv64)")
        if d:
            n64p = _normalize_n64_inst_input(d)
            self._n64.set(str(n64p) if n64p else d)

    def _browse_repo(self) -> None:
        d = filedialog.askdirectory(title="Repo root (contains Makefile and filesystem)")
        if d:
            self._repo.set(d)

    def _log_line(self, s: str) -> None:
        self._log.config(state=tk.NORMAL)
        self._log.insert(tk.END, s + "\n")
        self._log.see(tk.END)
        self._log.config(state=tk.DISABLED)

    def _pump_queue(self) -> None:
        try:
            while True:
                kind, data = self._queue.get_nowait()
                if kind == "log":
                    assert isinstance(data, str)
                    self._log_line(data)
                elif kind == "err":
                    messagebox.showerror("Conversion failed", data or "Unknown error")
                    self._finish_worker()
                elif kind == "done":
                    self._log_line("Done.")
                    self._finish_worker()
        except queue.Empty:
            pass
        self.after(80, self._pump_queue)

    def _finish_worker(self) -> None:
        self._btn_go.config(state=tk.NORMAL)
        self._btn_cancel.config(state=tk.DISABLED)
        self._worker = None
        self._cancel.clear()

    def _parse_opt_float(self, s: str) -> float | None:
        s = s.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            raise ValueError(f"not a number: {s!r}")

    def _start(self) -> None:
        if self._worker:
            return
        paths = [Path(self._list.get(i)) for i in range(self._list.size())]
        if not paths:
            messagebox.showwarning("No files", "Add at least one video file.")
            return
        out = Path(self._out.get().strip() or ".").expanduser()
        repo_root = Path(self._repo.get().strip() or ".").expanduser()
        n64s = self._n64.get().strip()
        n64_norm = _normalize_n64_inst_input(n64s)
        n64_inst = n64_norm if n64_norm else None
        if n64_inst:
            self._n64.set(str(n64_inst))
            ac = find_audioconv64(n64_inst)
            if ac:
                self._log_line(f"Using audioconv64: {ac}")
            else:
                messagebox.showwarning(
                    "N64 toolchain path",
                    f"audioconv64 not found under:\n{n64_inst}\n\n"
                    "Conversion can still run, but wav64 output will be skipped until this path is fixed.",
                )
        stem = self._stem.get().strip() or None
        if stem and len(paths) > 1:
            messagebox.showwarning("Output name", "Custom output name works with only one file.")
            return
        mode = self._output_mode.get().strip()
        is_v2_mode = mode == MODE_V2
        if len(paths) != 1:
            messagebox.showwarning("Input file", "Use exactly one input file in these build modes.")
            return
        if is_v2_mode:
            if not (repo_root / "Makefile").is_file() or not (repo_root / "filesystem").is_dir():
                messagebox.showerror(
                    "EverDrive V2 mode",
                    "Repo root must contain Makefile and filesystem/.",
                )
                return
        else:
            if not (repo_root / "Makefile").is_file() or not (repo_root / "filesystem").is_dir():
                messagebox.showerror(
                    "X7/others mode",
                    "Repo root must contain Makefile, Makefile.sd and filesystem/.",
                )
                return
            if not (repo_root / "Makefile.sd").is_file():
                messagebox.showerror("X7/others mode", "Repo root missing Makefile.sd.")
                return
        try:
            fps = int(self._fps.get().strip())
            if not (1 <= fps <= 60):
                raise ValueError("FPS 1–60")
            ahz = int(self._ahz.get().strip())
        except ValueError as e:
            messagebox.showerror("Invalid settings", str(e))
            return
        try:
            t0 = self._parse_opt_float(self._t0.get())
            dur = self._parse_opt_float(self._dur.get())
        except ValueError as e:
            messagebox.showerror("Invalid time", str(e))
            return

        extra = self._vf_extra.get().strip()
        if self._gamma.get():
            extra = f"{extra},{'eq=gamma=0.4545'}" if extra else "eq=gamma=0.4545"

        opts = ConvertOptions(
            video_bitrate=self._vbit.get().strip() or "800K",
            fps=fps,
            scale=self._scale.get().strip() or "320:-16",
            vf_extra=extra,
            audio_hz=ahz,
            start_sec=t0,
            duration_sec=dur,
            no_audio=self._noaud.get(),
            keep_wav=self._keepwav.get(),
            skip_wav64=self._skip64.get(),
            auto_tune_long_videos=self._auto_tune.get(),
            force_cbr=self._force_cbr.get(),
            chunk_seconds=None,
            chunk_auto=False,
            fit_sd_preload=False,
        )

        self._log.config(state=tk.NORMAL)
        self._log.delete(1.0, tk.END)
        self._log.config(state=tk.DISABLED)
        self._btn_go.config(state=tk.DISABLED)
        self._btn_cancel.config(state=tk.NORMAL)
        self._cancel.clear()

        def run() -> None:
            def qlog(msg: str) -> None:
                self._queue.put(("log", msg))

            try:
                if is_v2_mode:
                    # EverDrive V2 route: single embedded ROM capped at 64MB.
                    convert_to_single_rom_fit(
                        paths[0],
                        out,
                        repo_root=repo_root,
                        n64_inst=n64_inst,
                        stem_override=stem,
                        opts=opts,
                        max_rom_mb=64.0,
                        rom_filename="n64video.z64",
                        log=qlog,
                        cancel=self._cancel.is_set,
                    )
                else:
                    # X7/others route: SD video-player menu ROM + one video/audio pair.
                    convert_to_menu_rom_bundle(
                        paths[0],
                        out,
                        repo_root=repo_root,
                        n64_inst=n64_inst,
                        stem_override=stem,
                        opts=opts,
                        build_engine_rom=True,
                        log=qlog,
                        cancel=self._cancel.is_set,
                    )
                self._queue.put(("done", None))
            except ConversionError as e:
                self._queue.put(("err", str(e)))
            except Exception as e:  # Keep GUI thread from crashing silently.
                self._queue.put(("err", f"Unexpected error: {e}"))

        self._worker = threading.Thread(target=run, daemon=True)
        self._worker.start()

    def _cancel_run(self) -> None:
        self._cancel.set()
        self._queue.put(("log", "Cancel requested…"))


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
