#!/usr/bin/env python3
"""
GUI wrapper for libdragon preview's videoconv64 (official MPEG-1 / H.264 + wav64 pipeline).

Requires:
  - videoconv64 on PATH or under N64_INST/bin (build libdragon preview tools)
  - ffmpeg / ffprobe on PATH (or set paths below)
  - audioconv64 reachable (usually same bin/ as videoconv64 — set N64_INST)

Run:  python scripts/video2n64_videoconv_gui.py
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


def _is_runnable(p: Path) -> bool:
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def find_videoconv64(n64_inst: str | None) -> Path | None:
    for name in ("videoconv64", "videoconv64.exe"):
        w = shutil.which(name)
        if w:
            return Path(w)
    prefixes: list[Path] = []
    if n64_inst:
        prefixes.append(Path(n64_inst).expanduser())
    env = os.environ.get("N64_INST", "").strip()
    if env:
        prefixes.append(Path(env).expanduser())
    home = Path.home()
    prefixes.extend(
        [
            home / "libdragon-n64-inst",
            home / "Projects" / "libdragon-n64-inst",
            home / "Projects" / "libdragon-preview",
            Path("/opt/libdragon"),
        ]
    )
    seen: set[str] = set()
    uniq = []
    for p in prefixes:
        k = str(p.resolve()) if p.exists() else str(p)
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    for prefix in uniq:
        for name in ("videoconv64", "videoconv64.exe"):
            cand = prefix / "bin" / name
            if _is_runnable(cand):
                return cand
    return None


def _guess_n64_inst() -> str:
    env = os.environ.get("N64_INST", "").strip()
    if env and Path(env).expanduser().is_dir():
        return env
    home = Path.home()
    for c in (
        home / "Projects" / "libdragon-n64-inst",
        home / "libdragon-n64-inst",
        home / "Projects" / "libdragon-preview",
        Path("/opt/libdragon"),
    ):
        if (c / "bin" / "audioconv64").is_file() or (c / "bin" / "videoconv64").is_file():
            return str(c)
    return ""


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("N64 FMV — videoconv64 (libdragon)")
        self.minsize(680, 620)
        self._queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._proc: subprocess.Popen[str] | None = None

        self._build()
        self.after(80, self._pump_queue)

    def _build(self) -> None:
        root_f = ttk.Frame(self, padding=8)
        root_f.pack(fill=tk.BOTH, expand=True)

        f_in = ttk.LabelFrame(root_f, text="Input", padding=6)
        f_in.pack(fill=tk.X)
        ttk.Label(f_in, text="Video file").grid(row=0, column=0, sticky=tk.W)
        self._video = tk.StringVar()
        ttk.Entry(f_in, textvariable=self._video, width=55).grid(
            row=0, column=1, sticky=tk.EW, padx=4
        )
        ttk.Button(f_in, text="Browse…", command=self._browse_video).grid(row=0, column=2)
        ttk.Label(f_in, text="Extra audio/sub files (optional)").grid(
            row=1, column=0, sticky=tk.NW, pady=(6, 0)
        )
        ex = ttk.Frame(f_in)
        ex.grid(row=1, column=1, columnspan=2, sticky=tk.EW, pady=(6, 0))
        self._extra = tk.Listbox(ex, height=4)
        self._extra.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(ex, command=self._extra.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._extra.config(yscrollcommand=sb.set)
        bf = ttk.Frame(ex)
        bf.pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(bf, text="Add…", command=self._add_extra).pack(fill=tk.X)
        ttk.Button(bf, text="Remove", command=self._rm_extra).pack(fill=tk.X, pady=4)
        f_in.columnconfigure(1, weight=1)

        f_out = ttk.LabelFrame(root_f, text="Output & tools", padding=6)
        f_out.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(f_out, text="Output folder").grid(row=0, column=0, sticky=tk.W)
        self._outdir = tk.StringVar(value=str(Path.cwd() / "videoconv-out"))
        ttk.Entry(f_out, textvariable=self._outdir, width=50).grid(
            row=0, column=1, sticky=tk.EW, padx=4
        )
        ttk.Button(f_out, text="Browse…", command=self._browse_out).grid(row=0, column=2)
        ttk.Label(f_out, text="N64_INST (audioconv64 in bin/)").grid(
            row=1, column=0, sticky=tk.W, pady=(4, 0)
        )
        self._n64 = tk.StringVar(value=_guess_n64_inst())
        ttk.Entry(f_out, textvariable=self._n64, width=50).grid(
            row=1, column=1, sticky=tk.EW, padx=4, pady=(4, 0)
        )
        ttk.Button(f_out, text="Browse…", command=self._browse_n64).grid(row=1, column=2, pady=(4, 0))
        ttk.Label(f_out, text="videoconv64 executable").grid(
            row=2, column=0, sticky=tk.W, pady=(4, 0)
        )
        self._vc_path = tk.StringVar(value="")
        self._vc_entry = ttk.Entry(f_out, textvariable=self._vc_path, width=50)
        self._vc_entry.grid(row=2, column=1, sticky=tk.EW, padx=4, pady=(4, 0))
        ttk.Button(f_out, text="Browse…", command=self._browse_vc).grid(row=2, column=2, pady=(4, 0))
        ttk.Button(f_out, text="Auto-find", command=self._autofind_vc).grid(row=2, column=3, padx=(4, 0))
        f_out.columnconfigure(1, weight=1)

        f_enc = ttk.LabelFrame(root_f, text="videoconv64 options", padding=6)
        f_enc.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(f_enc, text="Codec").grid(row=0, column=0, sticky=tk.W)
        self._codec = tk.StringVar(value="mpeg1")
        ttk.Combobox(
            f_enc,
            textvariable=self._codec,
            values=("mpeg1", "h264"),
            state="readonly",
            width=12,
        ).grid(row=0, column=1, sticky=tk.W, padx=4)
        ttk.Label(f_enc, text="Width").grid(row=0, column=2, sticky=tk.E, padx=(16, 4))
        self._width = tk.StringVar(value="320")
        ttk.Spinbox(f_enc, from_=96, to=512, textvariable=self._width, width=8).grid(
            row=0, column=3, sticky=tk.W
        )
        ttk.Label(f_enc, text="FPS (empty = auto)").grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        self._fps = tk.StringVar(value="")
        ttk.Entry(f_enc, textvariable=self._fps, width=10).grid(
            row=1, column=1, sticky=tk.W, padx=4, pady=(6, 0)
        )
        ttk.Label(f_enc, text="Quality 0–100").grid(row=1, column=2, sticky=tk.E, padx=(16, 4), pady=(6, 0))
        self._quality = tk.StringVar(value="80")
        ttk.Spinbox(f_enc, from_=0, to=100, textvariable=self._quality, width=8).grid(
            row=1, column=3, sticky=tk.W, pady=(6, 0)
        )
        ttk.Label(f_enc, text="Profile").grid(row=2, column=0, sticky=tk.W, pady=(6, 0))
        self._profile = tk.StringVar(value="auto")
        ttk.Combobox(
            f_enc,
            textvariable=self._profile,
            values=("auto", "cartoon", "film", "noisy", "none"),
            state="readonly",
            width=12,
        ).grid(row=2, column=1, sticky=tk.W, padx=4, pady=(6, 0))
        ttk.Label(f_enc, text="Deinterlace").grid(row=2, column=2, sticky=tk.E, padx=(16, 4), pady=(6, 0))
        self._deint = tk.StringVar(value="auto")
        ttk.Combobox(
            f_enc,
            textvariable=self._deint,
            values=("auto", "on", "off"),
            state="readonly",
            width=8,
        ).grid(row=2, column=3, sticky=tk.W, pady=(6, 0))
        ttk.Label(f_enc, text="Quant matrix").grid(row=3, column=0, sticky=tk.W, pady=(6, 0))
        self._qmatrix = tk.StringVar(value="n64")
        ttk.Combobox(
            f_enc,
            textvariable=self._qmatrix,
            values=("n64", "std"),
            state="readonly",
            width=12,
        ).grid(row=3, column=1, sticky=tk.W, padx=4, pady=(6, 0))
        ttk.Label(f_enc, text="--seek (sec or file)").grid(row=3, column=2, sticky=tk.E, padx=(16, 4), pady=(6, 0))
        self._seek = tk.StringVar(value="")
        ttk.Entry(f_enc, textvariable=self._seek, width=18).grid(
            row=3, column=3, sticky=tk.W, pady=(6, 0)
        )
        ttk.Label(f_enc, text="--audio-parms RATE,CH").grid(row=4, column=0, sticky=tk.W, pady=(6, 0))
        self._aparms = tk.StringVar(value="32000,1")
        ttk.Entry(f_enc, textvariable=self._aparms, width=14).grid(
            row=4, column=1, sticky=tk.W, padx=4, pady=(6, 0)
        )
        ttk.Label(f_enc, text="--audio-compress").grid(row=4, column=2, sticky=tk.E, padx=(16, 4), pady=(6, 0))
        self._acompress = tk.StringVar(value="")
        ttk.Entry(f_enc, textvariable=self._acompress, width=10).grid(
            row=4, column=3, sticky=tk.W, pady=(6, 0)
        )
        ttk.Label(f_enc, text="ffmpeg path").grid(row=5, column=0, sticky=tk.W, pady=(6, 0))
        self._ffm = tk.StringVar(value="ffmpeg")
        ttk.Entry(f_enc, textvariable=self._ffm, width=20).grid(
            row=5, column=1, sticky=tk.W, padx=4, pady=(6, 0)
        )
        ttk.Label(f_enc, text="ffprobe path").grid(row=5, column=2, sticky=tk.E, padx=(16, 4), pady=(6, 0))
        self._ffp = tk.StringVar(value="ffprobe")
        ttk.Entry(f_enc, textvariable=self._ffp, width=18).grid(row=5, column=3, sticky=tk.W, pady=(6, 0))

        f_chk = ttk.Frame(f_enc)
        f_chk.grid(row=6, column=0, columnspan=4, sticky=tk.W, pady=(8, 0))
        self._quick = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_chk, text="Quick (-Q)", variable=self._quick).pack(side=tk.LEFT)
        self._noaud = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_chk, text="No audio", variable=self._noaud).pack(side=tk.LEFT, padx=(12, 0))
        self._noprog = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_chk, text="No progress (--no-progress)", variable=self._noprog).pack(
            side=tk.LEFT, padx=(12, 0)
        )
        ttk.Label(f_chk, text="Verbose -v count").pack(side=tk.LEFT, padx=(16, 4))
        self._verbose = tk.StringVar(value="1")
        ttk.Spinbox(f_chk, from_=0, to=3, textvariable=self._verbose, width=4).pack(side=tk.LEFT)

        f_log = ttk.LabelFrame(root_f, text="Log", padding=4)
        f_log.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self._log = tk.Text(f_log, height=14, state=tk.DISABLED, wrap=tk.WORD, font=("monospace", 9))
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
            text="Outputs .m1v/.h264 + .wav64 in the output folder (see libdragon preview docs).",
        ).pack(side=tk.LEFT, padx=(12, 0))

        self._autofind_vc()

    def _browse_video(self) -> None:
        p = filedialog.askopenfilename(
            title="Input video",
            filetypes=[("Video", "*.mp4 *.mkv *.avi *.webm *.mov *.m4v"), ("All", "*.*")],
        )
        if p:
            self._video.set(p)

    def _browse_out(self) -> None:
        d = filedialog.askdirectory(title="Output folder")
        if d:
            self._outdir.set(d)

    def _browse_n64(self) -> None:
        d = filedialog.askdirectory(title="N64_INST (folder with bin/audioconv64)")
        if d:
            self._n64.set(d)

    def _browse_vc(self) -> None:
        p = filedialog.askopenfilename(
            title="videoconv64 executable",
            filetypes=[("Executable", "*.exe videoconv64*"), ("All", "*.*")],
        )
        if p:
            self._vc_path.set(p)

    def _add_extra(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Extra audio or subtitle file (single stream)",
            filetypes=[("Media", "*.*")],
        )
        for p in paths:
            self._extra.insert(tk.END, p)

    def _rm_extra(self) -> None:
        for i in reversed(self._extra.curselection()):
            self._extra.delete(i)

    def _autofind_vc(self) -> None:
        n64 = self._n64.get().strip()
        vc = find_videoconv64(n64 if n64 else None)
        if vc:
            self._vc_path.set(str(vc))

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
                    messagebox.showerror("videoconv64 failed", data or "Unknown error")
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
        self._proc = None
        self._cancel.clear()

    def _cancel_run(self) -> None:
        self._cancel.set()
        p = self._proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass
        self._queue.put(("log", "Cancel requested…"))

    def _start(self) -> None:
        if self._worker:
            return
        vin = Path(self._video.get().strip())
        if not vin.is_file():
            messagebox.showerror("Input", "Choose a valid video file.")
            return
        out = Path(self._outdir.get().strip() or ".").expanduser()
        vc_s = self._vc_path.get().strip()
        if not vc_s:
            self._autofind_vc()
            vc_s = self._vc_path.get().strip()
        vc = Path(vc_s) if vc_s else None
        if not vc or not vc.is_file():
            messagebox.showerror(
                "videoconv64",
                "Could not find videoconv64. Build libdragon preview tools or set the path.\n"
                "https://github.com/DragonMinded/libdragon/tree/preview/tools/videoconv64",
            )
            return
        try:
            w = int(self._width.get().strip())
            if not (1 <= w < 640):
                raise ValueError("width")
            q = int(self._quality.get().strip())
            if not (0 <= q <= 100):
                raise ValueError("quality")
            vbc = int(self._verbose.get().strip())
            if not (0 <= vbc <= 3):
                raise ValueError("verbose")
        except ValueError:
            messagebox.showerror("Settings", "Check width (1–639), quality (0–100), verbose (0–3).")
            return
        fps_s = self._fps.get().strip()
        if fps_s:
            try:
                fps = float(fps_s)
                if not (1.0 <= fps <= 60.0):
                    raise ValueError
            except ValueError:
                messagebox.showerror("FPS", "FPS must be empty (auto) or a number 1–60.")
                return
        else:
            fps = None
        seek_s = self._seek.get().strip()
        ap = self._aparms.get().strip()
        if ap and ap.count(",") != 1:
            messagebox.showerror("Audio", "--audio-parms must look like 32000,1")
            return

        out.mkdir(parents=True, exist_ok=True)
        extras = [self._extra.get(i) for i in range(self._extra.size())]

        n64_inst = self._n64.get().strip()
        env = os.environ.copy()
        if n64_inst:
            bin_dir = str(Path(n64_inst).expanduser() / "bin")
            env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
            env["N64_INST"] = str(Path(n64_inst).expanduser())

        argv: list[str] = [str(vc)]
        for _ in range(vbc):
            argv.append("-v")
        argv.extend(["-o", str(out.resolve())])
        argv.extend(["-c", self._codec.get().strip()])
        argv.extend(["-w", str(w)])
        if fps is not None:
            argv.extend(["-r", str(fps)])
        argv.extend(["-q", str(q)])
        if self._quick.get():
            argv.append("-Q")
        argv.extend(["--profile", self._profile.get().strip()])
        argv.extend(["--deinterlace", self._deint.get().strip()])
        argv.extend(["--quant-matrix", self._qmatrix.get().strip()])
        if seek_s:
            argv.extend(["--seek", seek_s])
        if self._noaud.get():
            argv.append("--no-audio")
        else:
            if ap:
                argv.extend(["--audio-parms", ap])
            ac = self._acompress.get().strip()
            if ac:
                argv.extend(["--audio-compress", ac])
        if self._noprog.get():
            argv.append("--no-progress")
        ffm = self._ffm.get().strip()
        if ffm and ffm != "ffmpeg":
            argv.extend(["--ffmpeg-path", ffm])
        ffp = self._ffp.get().strip()
        if ffp and ffp != "ffprobe":
            argv.extend(["--ffprobe-path", ffp])
        argv.append(str(vin.resolve()))
        for e in extras:
            argv.append(str(Path(e).resolve()))

        self._log.config(state=tk.NORMAL)
        self._log.delete(1.0, tk.END)
        self._log.config(state=tk.DISABLED)
        self._log_line("Command: " + subprocess.list2cmdline(argv))
        self._btn_go.config(state=tk.DISABLED)
        self._btn_cancel.config(state=tk.NORMAL)
        self._cancel.clear()

        def run() -> None:
            try:
                self._proc = subprocess.Popen(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env,
                )
                assert self._proc.stdout
                for line in self._proc.stdout:
                    if self._cancel.is_set():
                        break
                    self._queue.put(("log", line.rstrip("\n\r")))
                if self._cancel.is_set():
                    self._proc.terminate()
                    try:
                        self._proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self._proc.kill()
                    self._queue.put(("log", "Cancelled."))
                    self._queue.put(("done", None))
                    return
                rc = self._proc.wait()
                if rc != 0:
                    self._queue.put(("err", f"videoconv64 exited with code {rc}"))
                    return
                self._queue.put(("done", None))
            except Exception as e:
                self._queue.put(("err", str(e)))

        self._worker = threading.Thread(target=run, daemon=True)
        self._worker.start()


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
