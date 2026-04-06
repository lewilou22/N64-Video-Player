#!/usr/bin/env python3
"""
GUI: audio → .wav64 only (libdragon audioconv64). No video, no full toolchain.

Put audioconv64.exe next to this script, or set AUDIOCONV64 / N64_INST, or browse.
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from audio2wav64_lib import Wav64ConversionError, convert_many_wav64, find_audioconv64


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("N64 WAV64 — audio only (no toolchain)")
        self.minsize(640, 480)
        self._queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None

        self._build()
        self.after(80, self._pump_queue)

    def _build(self) -> None:
        root_f = ttk.Frame(self, padding=8)
        root_f.pack(fill=tk.BOTH, expand=True)

        f_files = ttk.LabelFrame(root_f, text="Input audio (.wav / .mp3 / .aiff)", padding=6)
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

        f_io = ttk.Frame(root_f)
        f_io.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(f_io, text="Output folder").grid(row=0, column=0, sticky=tk.W)
        self._out = tk.StringVar(value=str(Path.cwd() / "wav64-out"))
        ttk.Entry(f_io, textvariable=self._out, width=50).grid(
            row=0, column=1, sticky=tk.EW, padx=4
        )
        ttk.Button(f_io, text="Browse…", command=self._browse_out).grid(row=0, column=2)

        ttk.Label(f_io, text="audioconv64 location").grid(
            row=1, column=0, sticky=tk.W, pady=(6, 0)
        )
        hint = (
            "Folder with audioconv64.exe, path to .exe, or N64_INST root — "
            "leave empty to auto-detect (same folder as this app, N64_INST, PATH)"
        )
        self._tools = tk.StringVar(
            value=os.environ.get("N64_INST", "").strip()
            or os.environ.get("AUDIOCONV64", "").strip()
        )
        ttk.Entry(f_io, textvariable=self._tools, width=50).grid(
            row=1, column=1, sticky=tk.EW, padx=4, pady=(6, 0)
        )
        ttk.Button(f_io, text="Browse…", command=self._browse_tools).grid(
            row=1, column=2, pady=(6, 0)
        )
        ttk.Label(f_io, text=hint, wraplength=520, font=("TkDefaultFont", 8)).grid(
            row=2, column=0, columnspan=3, sticky=tk.W, pady=(4, 0)
        )
        f_io.columnconfigure(1, weight=1)

        f_opt = ttk.LabelFrame(root_f, text="audioconv64 options", padding=6)
        f_opt.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(f_opt, text="Compression").grid(row=0, column=0, sticky=tk.W)
        self._compress = tk.StringVar(value="1")
        ttk.Combobox(
            f_opt,
            textvariable=self._compress,
            values=("0", "1", "3"),
            width=6,
            state="readonly",
        ).grid(row=0, column=1, sticky=tk.W, padx=4)
        ttk.Label(f_opt, text="0=none, 1=VADPCM, 3=Opus").grid(
            row=0, column=2, sticky=tk.W, padx=(8, 0)
        )
        self._mono = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_opt, text="Mono", variable=self._mono).grid(
            row=1, column=0, sticky=tk.W, pady=(6, 0)
        )
        ttk.Label(f_opt, text="Resample Hz (empty=keep)").grid(
            row=1, column=1, sticky=tk.E, pady=(6, 0)
        )
        self._resample = tk.StringVar(value="")
        ttk.Entry(f_opt, textvariable=self._resample, width=10).grid(
            row=1, column=2, sticky=tk.W, padx=4, pady=(6, 0)
        )
        self._verbose = tk.BooleanVar(value=True)
        ttk.Checkbutton(f_opt, text="Verbose log", variable=self._verbose).grid(
            row=2, column=0, sticky=tk.W, pady=(6, 0)
        )

        f_log = ttk.LabelFrame(root_f, text="Log", padding=4)
        f_log.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self._log = tk.Text(f_log, height=12, state=tk.DISABLED, wrap=tk.WORD, font=("monospace", 9))
        self._log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lsb = ttk.Scrollbar(f_log, command=self._log.yview)
        lsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._log.config(yscrollcommand=lsb.set)

        f_btn = ttk.Frame(root_f)
        f_btn.pack(fill=tk.X, pady=(8, 0))
        self._btn_go = ttk.Button(f_btn, text="Convert to .wav64", command=self._start)
        self._btn_go.pack(side=tk.LEFT)
        self._btn_cancel = ttk.Button(f_btn, text="Cancel", command=self._cancel_run, state=tk.DISABLED)
        self._btn_cancel.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(
            f_btn,
            text="Bundle: copy audioconv64.exe next to this .py — no full libdragon install needed.",
        ).pack(side=tk.LEFT, padx=(12, 0))

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Audio files",
            filetypes=[
                ("Audio", "*.wav *.mp3 *.aiff *.aif"),
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

    def _browse_tools(self) -> None:
        p = filedialog.askopenfilename(
            title="audioconv64 executable",
            filetypes=[("audioconv64", "audioconv64.exe audioconv64"), ("All", "*.*")],
        )
        if p:
            self._tools.set(p)
            return
        d = filedialog.askdirectory(title="Folder containing audioconv64.exe (or N64_INST)")
        if d:
            self._tools.set(d)

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

    def _start(self) -> None:
        if self._worker:
            return
        paths = [Path(self._list.get(i)) for i in range(self._list.size())]
        if not paths:
            messagebox.showwarning("No files", "Add at least one audio file.")
            return
        out = Path(self._out.get().strip() or ".").expanduser()
        ts = self._tools.get().strip()
        tools_path = Path(ts).expanduser() if ts else None

        pre = find_audioconv64(tools_path)
        if not pre:
            messagebox.showerror(
                "audioconv64 not found",
                "Set location to folder with audioconv64.exe, full path to the .exe, "
                "or N64_INST. Or put audioconv64.exe next to this script.\n\n"
                "Env: AUDIOCONV64 or N64_INST.",
            )
            return

        extra = ["--wav-compress", self._compress.get().strip() or "1"]
        if self._mono.get():
            extra.append("--wav-mono")
        rs = self._resample.get().strip()
        if rs:
            try:
                hz = int(rs)
                if hz < 4000 or hz > 96000:
                    raise ValueError("out of range")
            except ValueError:
                messagebox.showerror("Resample", "Enter sample rate in Hz (e.g. 32000) or leave empty.")
                return
            extra.extend(["--wav-resample", str(hz)])

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
                convert_many_wav64(
                    paths,
                    out,
                    tools_path=tools_path,
                    extra_audioconv_args=extra,
                    verbose=self._verbose.get(),
                    log=qlog,
                    cancel=self._cancel.is_set,
                )
                self._queue.put(("done", None))
            except Wav64ConversionError as e:
                self._queue.put(("err", str(e)))

        self._worker = threading.Thread(target=run, daemon=True)
        self._worker.start()

    def _cancel_run(self) -> None:
        self._cancel.set()
        self._queue.put(("log", "Cancel requested…"))


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
