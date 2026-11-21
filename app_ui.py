"""Tkinter interface for the song looper.

The window lists every MIDI file in songs/ on the left and the selected song
on the right. The trigger key toggles playback (press once to start, again
to stop). The MIDI preview is always visible: it shows the notes and a
draggable three-octave playable range, and its bottom strip is the playback
bar — click or drag there to choose where playback starts. Trigger,
transpose and playthrough settings live in a separate Settings dialog.

MIDI parts can be layered: click Play on the parts you want, in priority
order. The first part decides the octave/Shift state for each chord; notes
from lower-priority parts that cannot share it are skipped. Auto re-runs the
automatic key choice, and "highest voice only" collapses every selected part
into one line. Octave changes are momentary in the game, so the macro just
holds q (low) or e (high) around the notes that need it; nothing needs
scheduling around them.
"""
import colorsys
import queue
import time
import tkinter as tk
from dataclasses import replace
from tkinter import ttk

import ui_theme as theme
from loop_player import LoopPlayer, key_display, reserved_reason
from play_macro import KEY_SHIFT_MAX, KEY_SHIFT_MIN, Options, plan_groups
from song_library import SongLibrary, band_base_low, key_name
from song_settings import SongSettings

# Shared palette, type scale and chrome (see ui_theme.py).
COLOR_IDLE = theme.DIM
COLOR_PLAY = theme.GOOD
COLOR_ERROR = theme.ERROR
COLOR_CAPTURE = theme.WARN
COLOR_PAUSE = "#c084fc"
PASS_COLORS = ("#f87171", "#4ade80", "#60a5fa", "#c084fc",
               "#fbbf24", "#22d3ee")


class App:
    def __init__(self, config):
        self.config = config
        self.root = tk.Tk()
        self.root.title("Lute — Song Looper")
        self.root.geometry("1300x840")
        self._min_w, self._min_h = 1120, 720
        self.root.minsize(self._min_w, self._min_h)
        self.root.configure(background=theme.BG)
        # Borderless, like the other Fathom tools: custom title bar, custom
        # resize grip, and an explicit taskbar entry for the shell.
        self.root.overrideredirect(True)
        theme.pin_taskbar(self.root)

        self.messages = queue.Queue()
        self.song_settings = SongSettings()
        self.library = SongLibrary(config, self.song_settings)
        self.player = LoopPlayer(config, self.messages)
        self.songs = {}
        self.part_choices = {}    # path -> ordered tuple of part indices
        self.window_choices = {}  # path -> lowest pitch of the playable window
        self.start_offsets = {}   # path -> song seconds where playback starts
        self.selected_path = None
        self.selected_song = None
        self._applied = None
        self._sig = None
        self._capturing = False
        self._pause_deadline = None
        self._pause_info = None

        # Playback bar state.
        self._pb_duration = 0.0
        self._pb_start = 0.0
        self._pb_pos = 0.0
        self._pb_drag = False

        # Settings dialog state (built on demand).
        self._settings_window = None
        self.capture_btn = None
        self.trigger_label = None

        self._parts_parts = None
        self._parts_focus = None

        self._preview = None
        self._preview_key = None
        self._preview_song = None
        self._preview_low = None
        self._preview_geom = None
        self._preview_duration = 0.0
        self._preview_origin = 0.0
        self._preview_items_ok = False
        self._preview_selected = None
        self._preview_resize_id = None
        self._drag_offset = None
        self._preview_split = None

        self._build_style()
        self._build_widgets()

        self.player.start()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._refresh_songs(force=True)
        self._set_idle_status()
        self._poll_id = self.root.after(80, self._poll)
        self._auto_id = self.root.after(int(config.get("poll_ms", 1000)),
                                        self._auto_refresh)

    # ------------------------------------------------------------------- style
    def _build_style(self):
        theme.style_widgets(self.root)

    # ----------------------------------------------------------------- widgets
    def _build_widgets(self):
        self.transpose_var = tk.StringVar(
            value=str(int(self.config.get("transpose", 0))))
        self.split_var = tk.StringVar(
            value=str(int(self.config.get("split_passes", 1))))
        self.pass_pause_var = tk.StringVar(
            value=f"{float(self.config.get('pass_pause', 10.0)):g}")
        self.top_voice_var = tk.BooleanVar(
            value=bool(self.config.get("top_voice", False)))
        self.bpm_var = tk.StringVar()

        # ---- window chrome --------------------------------------------------
        bar, bar_bg, bar_button = theme.title_bar(
            self.root, "Lute", self._close, subtitle="Song Looper")
        self.titlebar = bar
        bar_button("Settings", self._open_settings)
        self.trigger_header_label = tk.Label(
            bar, text="", bg=bar_bg, fg=theme.DIM, font=theme.MONO_SMALL,
            padx=10)
        self.trigger_header_label.pack(side="right", fill="y")
        self._update_trigger_header()

        status = tk.Frame(self.root, bg=theme.BAR, height=theme.STATUS_HEIGHT)
        status.pack(side="bottom", fill="x")
        status.pack_propagate(False)
        self.status_label = tk.Label(status, text="", bg=theme.BAR,
                                     fg=theme.DIM, font=theme.MONO_SMALL,
                                     anchor="w", padx=8)
        self.status_label.pack(side="left", fill="x", expand=True)
        theme.resize_grip(status, self._grip_press, self._grip_drag)

        # ---- content --------------------------------------------------------
        outer = tk.Frame(self.root, bg=theme.BG, padx=10, pady=8)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        # ---- main: songs on the left, player on the right -------------------
        main = tk.Frame(outer, bg=theme.BG)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=0, minsize=420)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        songs, songs_head, songs_body = theme.panel(main, "Songs")
        songs.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        songs_body.columnconfigure(0, weight=1)
        songs_body.rowconfigure(0, weight=1)
        theme.flat_button(songs_head, "Reload", self._reload, width=8).pack(
            side="right")

        columns = ("key", "bpm", "length")
        self.tree = ttk.Treeview(songs_body, columns=columns,
                                 show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Song", anchor="w")
        self.tree.heading("key", text="Key")
        self.tree.heading("bpm", text="BPM")
        self.tree.heading("length", text="Length")
        self.tree.column("#0", width=260, minwidth=180, stretch=True, anchor="w")
        self.tree.column("key", width=68, minwidth=58, anchor="center",
                         stretch=False)
        self.tree.column("bpm", width=56, minwidth=50, anchor="center",
                         stretch=False)
        self.tree.column("length", width=70, minwidth=62, anchor="center",
                         stretch=False)
        self.tree.tag_configure("error", foreground=COLOR_ERROR)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        scroll = theme.ThemedScrollbar(songs_body, command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns", padx=(3, 0))
        self.tree.configure(yscrollcommand=scroll.set)

        self.player_panel = tk.Frame(main, bg=theme.BG)
        self.player_panel.grid(row=0, column=1, sticky="nsew")
        self.player_panel.columnconfigure(0, weight=1)
        self.player_panel.rowconfigure(1, weight=1)

        transport, transport_head, transport_body = theme.panel(
            self.player_panel, "Now playing")
        transport.grid(row=0, column=0, sticky="ew")
        transport_body.columnconfigure(0, weight=1)
        self.now_title = tk.Label(transport_body, text="No song selected",
                                  bg=theme.PANEL, fg=theme.TITLE,
                                  font=theme.DISPLAY, anchor="w")
        self.now_title.grid(row=0, column=0, sticky="w")
        bpm_box = tk.Frame(transport_body, bg=theme.PANEL)
        bpm_box.grid(row=0, column=1, sticky="e", padx=(12, 0))
        tk.Label(bpm_box, text="BPM", bg=theme.PANEL, fg=theme.DIM,
                 font=theme.SMALL).pack(side="left", padx=(0, 5))
        self.bpm_spin = ttk.Spinbox(
            bpm_box, from_=10, to=1000, increment=1, width=7,
            textvariable=self.bpm_var, command=self._on_bpm_change,
            style="Dark.TSpinbox", state="disabled")
        self.bpm_spin.pack(side="left")
        self.bpm_spin.bind("<Return>", self._on_bpm_change)
        self.bpm_spin.bind("<FocusOut>", self._on_bpm_change)
        self.details = tk.Label(transport_body, text="", bg=theme.PANEL,
                                fg=theme.DIM, font=theme.SMALL,
                                wraplength=760, justify="left", anchor="w")
        self.details.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # ---- MIDI parts (fills the player area) -----------------------------
        parts, parts_head, parts_body = theme.panel(
            self.player_panel, "MIDI parts")
        self.part_bar = parts
        parts.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        parts_body.columnconfigure(0, weight=1)
        parts_body.rowconfigure(0, weight=1)
        theme.Toggle(parts_head, "Highest voice only",
                     self.top_voice_var,
                     self._toggle_top_voice).pack(side="left", padx=(14, 0))
        for text, width, command in reversed((
                ("All", 5, self._select_all_parts),
                ("Auto", 5, self._auto_settings),
                ("▲", 2, lambda: self._move_part(-1)),
                ("▼", 2, lambda: self._move_part(1)))):
            theme.flat_button(parts_head, text, command, width=width,
                              padx=4, pady=1).pack(side="right", padx=(4, 0))

        self.part_tree = ttk.Treeview(parts_body, columns=("part", "play"),
                                      show="headings", height=8,
                                      selectmode="none", style="Parts.Treeview")
        self.part_tree.grid(row=0, column=0, sticky="nsew")
        self.part_tree.heading("part", text="Part", anchor="w")
        self.part_tree.heading("play", text="Play", anchor="center")
        self.part_tree.column("part", width=300, minwidth=160, stretch=True,
                              anchor="w")
        self.part_tree.column("play", width=46, minwidth=46, stretch=False,
                              anchor="center")
        self.part_tree.tag_configure("focus", background=theme.HOVER)
        self.part_tree.bind("<Button-1>", self._on_part_click)
        part_scroll = theme.ThemedScrollbar(parts_body,
                                            command=self.part_tree.yview)
        part_scroll.grid(row=0, column=1, sticky="ns", padx=(3, 0))
        part_xscroll = theme.ThemedScrollbar(parts_body, orient="horizontal",
                                             command=self.part_tree.xview)
        part_xscroll.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        self.part_tree.configure(yscrollcommand=part_scroll.set,
                                 xscrollcommand=part_xscroll.set)
        self.part_bar.grid_remove()
        self.player_panel.rowconfigure(1, weight=0)

        # ---- preview (full width, below everything) -------------------------
        self.preview_frame, _preview_head, preview_body = theme.panel(
            outer, "Preview")
        self.preview_frame.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        preview_body.columnconfigure(0, weight=1)
        self.preview = tk.Canvas(preview_body, height=210, bg=theme.BG,
                                 highlightthickness=0)
        self.preview.grid(row=0, column=0, sticky="nsew")
        self.preview.bind("<Configure>", self._on_preview_configure)
        self.preview.bind("<Button-1>", self._on_preview_press)
        self.preview.bind("<B1-Motion>", self._on_preview_drag)
        self.preview.bind("<ButtonRelease-1>", self._on_preview_release)
        self.preview.bind("<Motion>", self._on_preview_hover)

    def _update_trigger_header(self):
        self.trigger_header_label.configure(
            text=f"Trigger: {key_display(self.player.get_trigger())}")

    # ------------------------------------------------------------- window chrome
    def _grip_press(self, event):
        self._grip_start = (event.x_root, event.y_root,
                            self.root.winfo_width(),
                            self.root.winfo_height())

    def _grip_drag(self, event):
        x0, y0, w0, h0 = self._grip_start
        w = max(self._min_w, w0 + event.x_root - x0)
        h = max(self._min_h, h0 + event.y_root - y0)
        self.root.geometry(f"{w}x{h}")

    def _open_settings(self):
        win = self._settings_window
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_set()
            return
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.resizable(False, False)
        win.configure(bg=theme.BG)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        self._settings_window = win
        theme.title_bar(win, "Settings", self._close_settings)

        body = tk.Frame(win, bg=theme.BG, padx=10, pady=8)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        trigger_box, _trigger_head, trigger_body = theme.panel(
            body, "Trigger key · press to start/stop")
        trigger_box.grid(row=0, column=0, sticky="ew")
        trigger_body.columnconfigure(0, weight=1)
        self.trigger_label = tk.Label(
            trigger_body, text=key_display(self.player.get_trigger()),
            bg=theme.PANEL, fg=theme.TITLE, font=theme.BIG, anchor="w")
        self.trigger_label.grid(row=0, column=0, sticky="w")
        self.capture_btn = theme.flat_button(
            trigger_body, "Change…", self._toggle_capture, width=10)
        self.capture_btn.grid(row=0, column=1, sticky="e", padx=(10, 0))

        transpose_box, _transpose_head, transpose_body = theme.panel(
            body, "Transpose")
        transpose_box.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        transpose_body.columnconfigure(1, weight=1)
        tk.Label(transpose_body, text="Semitones", bg=theme.PANEL,
                 fg=theme.DIM, font=theme.SMALL).grid(row=0, column=0,
                                                      sticky="w")
        spin = ttk.Spinbox(transpose_body, from_=-24, to=24, increment=1,
                           width=5, textvariable=self.transpose_var,
                           command=self._on_transpose_change,
                           style="Dark.TSpinbox")
        spin.grid(row=0, column=1, sticky="w", padx=(8, 0))
        spin.bind("<Return>", self._on_transpose_change)
        spin.bind("<FocusOut>", self._on_transpose_change)

        split_box, _split_head, split_body = theme.panel(
            body, "Playthroughs")
        split_box.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        split_body.columnconfigure(1, weight=1)
        tk.Label(split_body, text="Passes", bg=theme.PANEL, fg=theme.DIM,
                 font=theme.SMALL).grid(row=0, column=0, sticky="w")
        split_spin = ttk.Spinbox(split_body, from_=1, to=4, increment=1,
                                 width=4, textvariable=self.split_var,
                                 command=self._on_split_change,
                                 style="Dark.TSpinbox")
        split_spin.grid(row=0, column=1, sticky="w", padx=(8, 0))
        split_spin.bind("<Return>", self._on_split_change)
        split_spin.bind("<FocusOut>", self._on_split_change)
        tk.Label(split_body, text="Pause", bg=theme.PANEL, fg=theme.DIM,
                 font=theme.SMALL).grid(row=1, column=0, sticky="w",
                                        pady=(6, 0))
        pause_spin = ttk.Spinbox(split_body, from_=0, to=120, increment=1,
                                 width=5, textvariable=self.pass_pause_var,
                                 command=self._on_pass_pause_change,
                                 style="Dark.TSpinbox")
        pause_spin.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        pause_spin.bind("<Return>", self._on_pass_pause_change)
        pause_spin.bind("<FocusOut>", self._on_pass_pause_change)
        tk.Label(split_body, text="s", bg=theme.PANEL, fg=theme.DIM,
                 font=theme.MONO_SMALL).grid(row=1, column=2, sticky="w",
                                             padx=(4, 0), pady=(6, 0))

        theme.flat_button(body, "Close", self._close_settings).grid(
            row=3, column=0, sticky="e", pady=(10, 0))
        win.protocol("WM_DELETE_WINDOW", self._close_settings)
        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width()
                                       - win.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height()
                                       - win.winfo_reqheight()) // 3
        win.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _close_settings(self):
        if self._capturing:
            self.player.cancel_capture()
            self._end_capture()
        win = self._settings_window
        self._settings_window = None
        self.capture_btn = None
        self.trigger_label = None
        if win is not None and win.winfo_exists():
            win.destroy()

    # ----------------------------------------------------------- playback bar
    def _song_duration(self, song):
        return (max(0.0, float(song.duration))
                if song is not None and song.ok else 0.0)

    def _start_for(self, song):
        if song is None or not song.ok:
            return 0.0
        value = float(self.start_offsets.get(song.path, 0.0))
        return max(0.0, min(value, max(0.0, song.duration - 0.05)))

    def _set_playback_song(self, song):
        self._pb_duration = self._song_duration(song)
        self._pb_start = self._start_for(song)
        self._pb_pos = self._pb_start
        self._draw_transport()

    def _update_playback_progress(self):
        if self._pb_drag or getattr(self, "preview", None) is None:
            return
        pos = self.player.position()
        self._pb_pos = self._pb_start if pos is None else pos
        self._draw_transport()

    def _commit_start(self, seconds):
        song = self.selected_song
        if song is None or not song.ok:
            self._pb_start = 0.0
            self._pb_pos = 0.0
            self._draw_transport()
            return
        seconds = max(0.0, min(seconds, max(0.0, self._pb_duration - 0.05)))
        self.start_offsets[song.path] = seconds
        self._pb_start = seconds
        self._pb_pos = seconds
        self.player.set_start(seconds)
        self._draw_transport()
        self._set_status(f"Start set to {_fmt_clock(seconds)} — press "
                         f"{self._trigger_text()} to play", "idle")

    # ------------------------------------------------------------------- songs
    def _reload(self):
        self._refresh_songs(force=True)

    def _refresh_songs(self, force=False):
        songs, signature = self.library.scan()
        if not force and signature == self._sig:
            return
        self._sig = signature
        selected = self.selected_path

        # Re-apply any MIDI parts / playable window picked earlier this session.
        self.songs = {}
        base_low = band_base_low(self.config.get("mid_octave", 4))
        for base in songs:
            song = base
            choice = self.part_choices.get(base.path)
            if choice is not None and (not choice or any(
                    not 0 <= i < len(base.parts) for i in choice)):
                del self.part_choices[base.path]
                choice = None
            window = self.window_choices.get(base.path)
            window_min = base_low + KEY_SHIFT_MIN
            window_max = base_low + KEY_SHIFT_MAX
            if window is not None and not (window_min <= window <= window_max):
                del self.window_choices[base.path]
                window = None
            if choice is not None or window is not None:
                song = self.library.with_parts(
                    base, choice if choice is not None else base.part_indices,
                    window)
            self.songs[base.path] = song
        for path in list(self.part_choices):
            if path not in self.songs:
                del self.part_choices[path]
        for path in list(self.window_choices):
            if path not in self.songs:
                del self.window_choices[path]
        for path in list(self.start_offsets):
            if path not in self.songs:
                del self.start_offsets[path]

        self.tree.delete(*self.tree.get_children())
        order = [base.path for base in songs]
        for path in order:
            song = self.songs[path]
            self.tree.insert("", "end", iid=path,
                             text=("⚠ " if not song.ok else "") + song.name,
                             values=self._row_values(song),
                             tags=("error",) if not song.ok else ())

        if selected in self.songs:
            chosen = selected
        elif order:
            chosen = next((p for p in order if self.songs[p].ok), order[0])
        else:
            chosen = None

        if chosen is None:
            self.selected_path = None
            self.selected_song = None
            self._applied = None
            self._parts_parts = None
            self._parts_focus = None
            self.part_tree.delete(*self.part_tree.get_children())
            self.part_bar.grid_remove()
            self._clear_preview()
            self.player.clear_song()
            self._set_playback_song(None)
            self._show_empty_library()
            return

        self.selected_path = chosen
        self.tree.selection_set(chosen)
        self.tree.see(chosen)
        self._apply_song(self.songs[chosen])

    def _row_values(self, song):
        return (
            song.key_name if song.ok else "—",
            f"{song.bpm:g}" if song.ok else "—",
            _fmt_length(song.duration) if song.ok else "—",
        )

    def _update_row(self, song):
        if not self.tree.exists(song.path):
            return
        self.tree.item(song.path,
                       text=("⚠ " if not song.ok else "") + song.name,
                       values=self._row_values(song),
                       tags=("error",) if not song.ok else ())

    # -------------------------------------------------------------- MIDI parts
    @staticmethod
    def _part_tag(index, included):
        return f"part{index}{'on' if included else 'off'}"

    def _part_color(self, index):
        hue = (index * 0.61803398875 + 0.08) % 1.0
        r, g, b = colorsys.hsv_to_rgb(hue, 0.68, 0.86)
        return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))

    def _update_part_bar(self, song):
        if not song.parts:
            self._parts_parts = None
            self._parts_focus = None
            self.part_tree.delete(*self.part_tree.get_children())
            self.part_bar.grid_remove()
            self.player_panel.rowconfigure(1, weight=0)
            return

        rebuilt = False
        if self._parts_parts is not song.parts:
            rebuilt = True
            self._parts_parts = song.parts
            self._parts_focus = None
            self.part_tree.delete(*self.part_tree.get_children())
            for part in song.parts:
                color = self._part_color(part.index)
                self.part_tree.tag_configure(self._part_tag(part.index, True),
                                             foreground=color)
                self.part_tree.tag_configure(self._part_tag(part.index, False),
                                             foreground=_dim_color(color))
                self.part_tree.insert(
                    "", "end", iid=str(part.index),
                    values=(f"{part.index + 1}. {part.option_text}", "–"))

        self._refresh_part_rows(song)
        self.part_bar.grid()
        self.player_panel.rowconfigure(1, weight=1)
        if rebuilt and song.part_indices:
            iid = str(song.part_indices[0])
            if self.part_tree.exists(iid):
                self.part_tree.see(iid)

    def _refresh_part_rows(self, song):
        priority = {index: rank + 1 for rank, index in enumerate(song.part_indices)}
        for part in song.parts:
            iid = str(part.index)
            if not self.part_tree.exists(iid):
                continue
            included = part.index in priority
            self.part_tree.set(iid, "play",
                               str(priority[part.index]) if included else "–")
            tags = [self._part_tag(part.index, included)]
            if part.index == self._parts_focus:
                tags.append("focus")
            self.part_tree.item(iid, tags=tuple(tags))

    def _on_part_click(self, event):
        iid = self.part_tree.identify_row(event.y)
        if not iid:
            return
        try:
            index = int(iid)
        except ValueError:
            return
        song = self.songs.get(self.selected_path)
        if song is None:
            return
        self._parts_focus = index
        if self.part_tree.identify_column(event.x) != "#2":
            # A name click only moves the reorder focus.
            self._refresh_part_rows(song)
            return
        order = list(song.part_indices)
        if index in order:
            if len(order) == 1:
                self._set_status("Choose at least one MIDI part.", "error")
                return
            order.remove(index)
        else:
            order.append(index)
        self._apply_part_order(order)

    def _select_all_parts(self):
        song = self.songs.get(self.selected_path)
        if song is None or not song.parts:
            return
        order = list(song.part_indices)
        for part in song.parts:
            if part.index not in order:
                order.append(part.index)
        self._apply_part_order(order)

    def _move_part(self, delta):
        song = self.songs.get(self.selected_path)
        if song is None:
            return
        if self._parts_focus is None:
            self._set_status("Click a part first, then use ▲ / ▼.", "error")
            return
        order = list(song.part_indices)
        if self._parts_focus not in order:
            return
        i = order.index(self._parts_focus)
        j = i + delta
        if not 0 <= j < len(order):
            return
        order[i], order[j] = order[j], order[i]
        self._apply_part_order(order)
        if self.part_tree.exists(str(self._parts_focus)):
            self.part_tree.see(str(self._parts_focus))

    def _apply_part_order(self, order):
        song = self.songs.get(self.selected_path)
        if song is None:
            return
        new_song = self.library.with_parts(song, order,
                                           self.window_choices.get(song.path))
        if new_song is song:
            self._refresh_part_rows(song)
            return
        self.part_choices[song.path] = tuple(order)
        self.songs[song.path] = new_song
        self._update_row(new_song)
        self._apply_song(new_song)
        self._persist_settings(new_song)

    def _auto_settings(self):
        """Throw away the pinned window and let arrange() pick the key."""
        song = self.songs.get(self.selected_path)
        if song is None or song.midi is None:
            return
        self.window_choices.pop(song.path, None)
        parts = song.part_indices or tuple(range(len(song.parts)))
        new_song = self.library.with_parts(song, parts, None, auto=True)
        self.songs[song.path] = new_song
        self._update_row(new_song)
        self._apply_song(new_song)
        self._persist_settings(new_song)
        if new_song.ok and new_song.split:
            self._set_status(
                f"Rebuilt {len(new_song.passes)} automatic playback pass(es)",
                "idle")
        elif new_song.ok:
            self._set_status(
                f"Auto settings: key {new_song.key_name}", "idle")
        else:
            self._set_status(f"⚠ {new_song.name}: {new_song.error}", "error")

    def _toggle_top_voice(self):
        top = bool(self.top_voice_var.get())
        if top == bool(self.config.get("top_voice", False)):
            return
        self.config["top_voice"] = top
        self._rebuild_after_config()

    def _rebuild_after_config(self):
        """Re-apply a CONFIG change that affects how every song is built."""
        self.library.invalidate()
        self._applied = None
        self._refresh_songs(force=True)
        if self.selected_song is not None:
            self._set_idle_status()

    def _on_split_change(self, _event=None):
        try:
            value = int(self.split_var.get().strip())
        except ValueError:
            value = int(self.config.get("split_passes", 1))
            self.split_var.set(str(value))
            return
        value = max(1, min(4, value))
        self.split_var.set(str(value))
        if value == int(self.config.get("split_passes", 1)):
            return
        self.config["split_passes"] = value
        self._rebuild_after_config()
        if value == 1:
            self._set_status("Normal single-pass playback", "idle")

    def _on_pass_pause_change(self, _event=None):
        try:
            value = float(self.pass_pause_var.get().strip())
        except ValueError:
            value = float(self.config.get("pass_pause", 10.0))
            self.pass_pause_var.set(f"{value:g}")
            return
        value = max(0.0, min(120.0, value))
        self.pass_pause_var.set(f"{value:g}")
        if value == float(self.config.get("pass_pause", 10.0)):
            return
        self.config["pass_pause"] = value
        if self.selected_song is not None:
            self._apply_song(self.selected_song)

    def _on_transpose_change(self, _event=None):
        try:
            value = int(self.transpose_var.get().strip())
        except ValueError:
            value = int(self.config.get("transpose", 0))
            self.transpose_var.set(str(value))
            return
        self._set_transpose(value)

    def _set_transpose(self, value):
        value = max(-24, min(24, value))
        self.transpose_var.set(str(value))
        if value == int(self.config.get("transpose", 0)):
            return
        self.config["transpose"] = value
        # A pinned playable band is an absolute pitch range; the notes just
        # moved, so let the automatic choice pick a fresh one.
        self.window_choices.clear()
        self._rebuild_after_config()
        self._set_status(f"Transposed every song by {value:+d} semitone(s)",
                         "idle")

    # ------------------------------------------------------------------- tempo
    def _set_bpm_control(self, song):
        if getattr(self, "bpm_spin", None) is None:
            return
        if song is None or song.midi is None:
            self.bpm_var.set("")
            self.bpm_spin.configure(state="disabled")
            return
        self.bpm_var.set(f"{float(song.bpm):g}")
        self.bpm_spin.configure(state="normal")

    def _on_bpm_change(self, _event=None):
        song = self.songs.get(self.selected_path)
        if song is None or song.midi is None:
            return
        try:
            value = float(self.bpm_var.get().strip())
        except (TypeError, ValueError):
            self.bpm_var.set(f"{float(song.bpm):g}")
            return
        value = max(10.0, min(1000.0, value))
        self.bpm_var.set(f"{value:g}")
        # The spinbox shows a rounded BPM (e.g. 131 for 131.0001); don't
        # rewrite the tempo just because the field lost focus.
        if abs(value - float(song.bpm)) < 0.05:
            return
        new_song = self.library.with_bpm(song, value)
        if new_song is song:
            return
        self.songs[song.path] = new_song
        self._update_row(new_song)
        self._persist_settings(new_song)
        self._apply_song(new_song)
        if new_song.ok:
            self._set_status(
                f"Tempo set to {value:g} BPM — press {self._trigger_text()} "
                f"to play", "idle")
        else:
            self._set_status(f"⚠ {new_song.name}: {new_song.error}", "error")

    def _persist_settings(self, song):
        """Write this song's tempo, instruments and key to SongSettings.json."""
        if self.song_settings is None or song is None or song.midi is None:
            return
        base_bpm = float(song.midi.base_bpm or song.bpm)
        data = {
            "bpm": (round(float(song.bpm), 6)
                    if abs(float(song.bpm) - base_bpm) > 0.0005 else None),
            "parts": list(song.part_indices) if song.part_indices else None,
        }
        if song.split:
            data["passes"] = [
                {"key": key_name(p.key_shift), "key_shift": int(p.key_shift)}
                for p in song.passes]
            data["key"] = None
            data["key_shift"] = None
        else:
            data["passes"] = None
            if song.key_shift is not None:
                data["key"] = song.key_name
                data["key_shift"] = int(song.key_shift)
            # When the song is currently unplayable, leave any previously
            # saved key alone rather than erasing it.
        self.song_settings.update(song.name, **data)

    # ------------------------------------------------------------- MIDI preview
    def _song_origin(self, song):
        """Raw second that the playback timeline treats as time 0."""
        if song is None:
            return 0.0
        if song.split:
            return float(song.timeline_origin or 0.0)
        if song.groups:
            return float(song.groups[0]["t"])
        return 0.0

    def _clear_preview(self):
        """Show the empty preview (no song selected)."""
        self._preview = None
        self._preview_key = None
        self._preview_song = None
        self._preview_items_ok = False
        self._preview_selected = None
        self._preview_split = None
        self._preview_duration = 0.0
        self._preview_origin = 0.0
        self._drag_offset = None
        self._pb_drag = False
        self._preview_low = band_base_low(self.config.get("mid_octave", 4))
        self.preview_frame.grid()
        self.preview.update_idletasks()
        self._draw_preview()

    def _set_preview(self, song):
        if song.midi is None:
            self._clear_preview()
            return

        cache_key = (song.path, song.mtime_ns, song.size,
                     round(float(song.bpm), 6),
                     int(self.config.get("transpose", 0)))
        data_changed = self._preview_key != cache_key
        if data_changed:
            self._preview_key = cache_key
            transpose = self.config.get("transpose", 0)
            parts_data, duration = [], 0.0
            for part in song.midi.parts:
                intervals = [(start, end, pitch + transpose)
                             for start, end, pitch
                             in song.midi.part_intervals(part.index)]
                parts_data.append((part.index, intervals))
                for _start, end, _pitch in intervals:
                    duration = max(duration, end)
            self._preview = {"parts": parts_data, "duration": duration}
            self._preview_items_ok = False

        self._preview_song = song
        self._drag_offset = None
        self._preview_split = song.passes if song.split else None
        self._preview_origin = self._song_origin(song)
        if song.split:
            # The passes have their own windows; there is no single band to
            # drag. The preview draws one strip per pass instead.
            self._preview_low = None
        else:
            low = self.window_choices.get(song.path)
            if low is None:
                low = song.window_low
            if low is None:
                low = band_base_low(self.config.get("mid_octave", 4))
            self._preview_low = low
        self.preview_frame.grid()
        self.preview.update_idletasks()
        if data_changed or not self._preview_items_ok:
            self._draw_preview()
        else:
            self._update_preview_selection()
            self._draw_band()
            self._draw_transport()

    def _on_preview_configure(self, _event=None):
        """Debounce the full redraw while the window is being resized."""
        if self._preview_resize_id is not None:
            try:
                self.preview.after_cancel(self._preview_resize_id)
            except tk.TclError:
                pass
        self._preview_resize_id = self.preview.after(120, self._draw_preview)

    def _update_preview_selection(self):
        if not self._preview or self._preview_song is None:
            return
        selected = set(self._preview_song.part_indices)
        if selected == self._preview_selected:
            return
        for index, _intervals in self._preview["parts"]:
            self.preview.itemconfigure(
                f"p{index}", stipple="" if index in selected else "gray50")
        self._preview_selected = selected

    def _draw_preview(self, _event=None):
        self.preview.delete("all")
        self._preview_geom = None
        self._preview_items_ok = False
        self._preview_selected = None
        width = self.preview.winfo_width()
        height = self.preview.winfo_height()
        # The bottom strip (42 px) holds the seek/transport bar; the pitch
        # plot ends above it so the two never overlap.
        left, right, top, bottom = 46, 12, 10, 42
        plot_w = width - left - right
        plot_h = height - top - bottom
        if plot_w <= 10 or plot_h <= 10:
            return

        parts = self._preview["parts"] if self._preview else []
        duration = self._preview["duration"] if self._preview else 0.0
        self._preview_duration = duration
        base_low = band_base_low(self.config.get("mid_octave", 4))
        pitches = [p for _index, intervals in parts
                   for _start, _end, p in intervals]
        if pitches:
            pitch_lo = min(pitches + [base_low + KEY_SHIFT_MIN]) - 1
            pitch_hi = max(pitches + [base_low + KEY_SHIFT_MAX + 35]) + 1
        else:
            # Nothing selected yet: show the whole range a key can select so
            # the playable band is still visible.
            pitch_lo = base_low + KEY_SHIFT_MIN - 1
            pitch_hi = base_low + KEY_SHIFT_MAX + 36
        step = plot_h / (pitch_hi - pitch_lo + 1)
        self._preview_geom = (left, top, plot_w, plot_h, step,
                              pitch_lo, pitch_hi)

        def y_top(pitch):
            return top + (pitch_hi - pitch) * step

        span = max(float(duration), 0.001)

        def x_of(seconds):
            return left + min(max(seconds / span, 0.0), 1.0) * plot_w

        # pitch grid (one line per C)
        for pitch in range(pitch_lo, pitch_hi + 1):
            if pitch % 12 == 0:
                y = y_top(pitch)
                self.preview.create_line(left, y, left + plot_w, y,
                                         fill=theme.HOVER)
                self.preview.create_text(left - 6, y + step / 2,
                                         text=f"C{pitch // 12 - 1}", anchor="e",
                                         fill="#5d6a78",
                                         font=theme.MONO_SMALL)

        # note bars, colored per instrument; selection toggles their stipple
        for index, intervals in parts:
            color = self._part_color(index)
            tag = f"p{index}"
            for start, end, pitch in intervals:
                y0 = y_top(pitch) + step * 0.12
                y1 = y_top(pitch) + step * 0.88
                x0 = x_of(start)
                x1 = max(x_of(end), x0 + 1)
                self.preview.create_rectangle(x0, y0, x1, y1, fill=color,
                                              outline="",
                                              tags=("note", tag))

        self._preview_items_ok = True
        self._update_preview_selection()
        self._draw_band()
        self._draw_transport()

    def _draw_band(self):
        self.preview.delete("band")
        if self._preview_split:
            self._draw_pass_bands()
            return
        if self._preview_geom is None or self._preview_low is None:
            return
        left, top, plot_w, _plot_h, step, _pitch_lo, pitch_hi = self._preview_geom
        low = self._preview_low
        y0 = top + (pitch_hi - (low + 35)) * step
        y1 = top + (pitch_hi - low + 1) * step
        self.preview.create_rectangle(left, y0, left + plot_w, y1,
                                      fill=theme.ACCENT, stipple="gray12",
                                      outline=theme.FOCUS, width=2, tags="band")
        # Keep the band behind the note bars; its own label stays on top.
        self.preview.tag_raise("note")
        shift = low - band_base_low(self.config.get("mid_octave", 4))
        self.preview.create_text(left + 8, y0 + 4, anchor="nw", tags="band",
                                 text=f"3 octaves · key {key_name(shift)}",
                                 fill="#cfd8e3", font=theme.BOLD)

    def _draw_pass_bands(self):
        """Show each split pass as a strip over the pitch range it plays."""
        if self._preview_geom is None:
            return
        left, top, plot_w, _plot_h, step, _pitch_lo, pitch_hi = (
            self._preview_geom)
        for pass_ in self._preview_split:
            color = PASS_COLORS[(pass_.number - 1) % len(PASS_COLORS)]
            y0 = top + (pitch_hi - pass_.high) * step
            y1 = top + (pitch_hi - pass_.low + 1) * step
            self.preview.create_rectangle(
                left, y0, left + plot_w, y1, fill=color, stipple="gray12",
                outline=color, width=2, tags="band")
            self.preview.create_text(
                left + 8, y0 + 3, anchor="nw", tags="band",
                text=f"Pass {pass_.number} · key "
                     f"{key_name(pass_.key_shift)}",
                fill=theme.FG, font=theme.BOLD)
        self.preview.tag_raise("note")

    # -------------------------------------------------------- seek / transport
    def _transport_top(self):
        """Top edge of the seek strip's hit area."""
        return self.preview.winfo_height() - 36

    def _transport_seconds(self, x):
        """Song seconds under the cursor on the preview's time axis."""
        if self._preview_geom is None:
            return 0.0
        left, plot_w = self._preview_geom[0], self._preview_geom[2]
        duration = max(0.0, getattr(self, "_preview_duration", 0.0))
        if plot_w <= 0 or duration <= 0 or self._pb_duration <= 0:
            return 0.0
        raw = (x - left) / plot_w * duration
        relative = raw - self._preview_origin
        limit = max(0.0, self._pb_duration - 0.05)
        return max(0.0, min(relative, limit))

    def _draw_transport(self):
        """Draw the playback bar along the bottom of the MIDI preview."""
        if not hasattr(self, "preview"):
            return
        self.preview.delete("transport")
        if self._preview_geom is None:
            return
        left, _top, plot_w, _plot_h, _step, _lo, _hi = self._preview_geom
        right = left + plot_w
        height = self.preview.winfo_height()
        y = height - 22
        duration = max(0.0, getattr(self, "_preview_duration", 0.0))
        self.preview.create_line(left, y, right, y, width=8,
                                 fill=theme.HOVER, capstyle="round",
                                 tags="transport")
        if duration > 0 and self._pb_duration > 0:
            def x_of(seconds):
                return left + max(0.0, min(1.0, seconds / duration)) * plot_w

            start_x = x_of(self._preview_origin + self._pb_start)
            pos_x = x_of(self._preview_origin + self._pb_pos)
            if pos_x > start_x + 0.5:
                self.preview.create_line(start_x, y, pos_x, y, width=8,
                                         fill=COLOR_PLAY, capstyle="round",
                                         tags="transport")
            self.preview.create_line(start_x, y - 11, start_x, y + 11,
                                     fill=theme.TITLE, width=2,
                                     tags="transport")
            self.preview.create_polygon(
                start_x - 4, y - 11, start_x + 4, y - 11, start_x, y - 6,
                fill=theme.TITLE, outline="", tags="transport")
            for i in (1, 2, 3):
                x = left + plot_w * i / 4
                self.preview.create_line(x, y + 7, x, y + 11,
                                         fill=theme.SCROLL_THUMB,
                                         tags="transport")
        self.preview.create_text(
            left, height - 5, anchor="sw",
            text=f"Start {_fmt_clock(self._pb_start)}",
            fill=theme.DIM, font=theme.MONO_SMALL, tags="transport")
        total = ("—" if self._pb_duration <= 0
                 else f"{_fmt_clock(self._pb_pos)} / "
                      f"{_fmt_clock(self._pb_duration)}")
        self.preview.create_text(right, height - 5, anchor="se", text=total,
                                 fill=theme.DIM, font=theme.MONO_SMALL,
                                 tags="transport")

    def _band_span(self):
        left, top, _plot_w, _plot_h, step, _pitch_lo, pitch_hi = self._preview_geom
        low = self._preview_low
        return (top + (pitch_hi - (low + 35)) * step,
                top + (pitch_hi - low + 1) * step)

    def _pitch_at(self, y):
        _left, top, _plot_w, _plot_h, step, _pitch_lo, pitch_hi = self._preview_geom
        return pitch_hi - (y - top) / step + 0.5

    def _on_preview_press(self, event):
        if self._preview_geom is None:
            return
        if event.y >= self._transport_top():
            # The bottom strip is the seek bar: click/drag to choose where
            # playback starts.
            if self._preview_song is None or self._pb_duration <= 0:
                return
            self._pb_drag = True
            self._pb_start = self._transport_seconds(event.x)
            self._pb_pos = self._pb_start
            self._draw_transport()
            return
        if (self._preview_split or self._preview_low is None
                or self._preview_song is None):
            return
        y0, y1 = self._band_span()
        if y0 - 4 <= event.y <= y1 + 4:
            self._drag_offset = self._pitch_at(event.y) - self._preview_low
            self.preview.configure(cursor="sb_v_double_arrow")

    def _on_preview_drag(self, event):
        if self._pb_drag:
            self._pb_start = self._transport_seconds(event.x)
            self._pb_pos = self._pb_start
            self._draw_transport()
            return
        if self._drag_offset is None:
            return
        base_low = band_base_low(self.config.get("mid_octave", 4))
        low = round(self._pitch_at(event.y) - self._drag_offset)
        low = max(base_low + KEY_SHIFT_MIN,
                  min(base_low + KEY_SHIFT_MAX, low))
        if low != self._preview_low:
            self._preview_low = low
            self._draw_band()

    def _on_preview_release(self, event):
        if self._pb_drag:
            self._pb_drag = False
            self._commit_start(self._transport_seconds(event.x))
            return
        if self._drag_offset is None:
            return
        self._drag_offset = None
        self.preview.configure(cursor="")
        song = self._preview_song
        if song is None or self._preview_low is None:
            return
        if song.ok and song.window_low == self._preview_low:
            return
        self._commit_window(self._preview_low)

    def _on_preview_hover(self, event):
        if self._preview_geom is None:
            self.preview.configure(cursor="")
            return
        if event.y >= self._transport_top() and self._pb_duration > 0:
            self.preview.configure(cursor="sb_h_double_arrow")
            return
        if (self._preview_split or self._drag_offset is not None
                or self._preview_low is None or self._preview_song is None):
            self.preview.configure(cursor="")
            return
        y0, y1 = self._band_span()
        inside = y0 - 4 <= event.y <= y1 + 4
        self.preview.configure(cursor="sb_v_double_arrow" if inside else "")

    def _commit_window(self, low):
        song = self.songs.get(self.selected_path)
        if song is None:
            return
        self.window_choices[song.path] = low
        new_song = self.library.with_parts(song, song.part_indices, low)
        if new_song is song:
            return
        self.songs[song.path] = new_song
        self._update_row(new_song)
        self._apply_song(new_song)
        self._persist_settings(new_song)

    # ------------------------------------------------------------------ select
    def _on_select(self, _event=None):
        selection = self.tree.selection()
        if not selection:
            return
        path = selection[0]
        self.selected_path = path
        if self.tree.exists(path):
            self.tree.see(path)
        song = self.songs.get(path)
        if song is not None:
            self._apply_song(song)

    def _options_for(self, song):
        """Planner options matching the current CONFIG and this song."""
        return Options(
            mid_octave=self.config.get("mid_octave", 4),
            key_shift=song.key_shift if song.key_shift is not None else 0,
            hold=self.config.get("hold", 0.05),
            lead=self.config.get("lead", 0.15),
            modifier_lead=self.config.get("modifier_lead", 0.03),
            modifier_tail=self.config.get("modifier_tail", 0.01),
        )

    def _apply_song(self, song):
        applied_key = (song.path, song.mtime_ns, song.size, song.key_shift,
                       song.part_indices, song.top_voice,
                       round(float(song.bpm), 6),
                       int(self.config.get("transpose", 0)),
                       int(self.config.get("split_passes", 1)),
                       float(self.config.get("pass_pause", 10.0)),
                       tuple((p.number, p.key_shift,
                              p.played, p.dropped, p.raw_notes)
                             for p in song.passes))
        if applied_key == self._applied:
            return

        self._pause_deadline = None
        self._pause_info = None
        self._set_bpm_control(song)
        self._update_part_bar(song)
        self._set_preview(song)

        if not song.ok:
            self.player.clear_song()
            self.selected_song = None
            self._applied = applied_key
            self.now_title.configure(text=song.name)
            self._set_playback_song(song)
            self.details.configure(
                text=f"⚠ {song.error}", foreground=COLOR_ERROR)
            self._set_status(f"⚠ {song.name}: {song.error}", "error")
            return

        if song.split:
            self._apply_split_song(song, applied_key)
            return

        options = self._options_for(song)
        try:
            events, _stats = plan_groups(song.groups, options)
        except ValueError as exc:
            self.player.clear_song()
            self.selected_song = None
            self._applied = None
            self.now_title.configure(text=song.name)
            self._set_playback_song(song)
            self.details.configure(text=f"⚠ {exc}", foreground=COLOR_ERROR)
            self._set_status(f"⚠ {song.name}: {exc}", "error")
            return

        length = (max(song.duration, events[-1][0] + 0.02)
                  if events else song.duration)
        start = self._start_for(song)
        self.player.set_song(events, length, duration=song.duration,
                             lead=options.lead, start=start)
        self.selected_song = song
        self._applied = applied_key
        self._set_playback_song(song)
        self._show_details(song, length)
        if not self._capturing:
            self._set_idle_status()

    def _apply_split_song(self, song, applied_key):
        """Plan every register pass onto one shared song timeline."""
        base = self._options_for(song)
        origin = song.timeline_origin
        stems = []
        last_event = 0.0
        try:
            for pass_ in song.passes:
                options = replace(base, key_shift=pass_.key_shift)
                events, _stats = plan_groups(pass_.groups, options,
                                             origin=origin)
                stems.append({
                    "events": events, "length": 0.0,
                    "info": {"number": pass_.number,
                             "key": key_name(pass_.key_shift)},
                })
                if events:
                    last_event = max(last_event, events[-1][0])
        except ValueError as exc:
            self.player.clear_song()
            self.selected_song = None
            self._applied = None
            self.now_title.configure(text=song.name)
            self._set_playback_song(song)
            self.details.configure(text=f"⚠ {exc}", foreground=COLOR_ERROR)
            self._set_status(f"⚠ {song.name}: {exc}", "error")
            return

        # Every pass runs the full song length, even when its own notes end
        # early, so the three recordings can be layered without shifting.
        length = max(base.lead + song.duration + 0.02, last_event + 0.02)
        for stem in stems:
            stem["length"] = length
        pause = float(self.config.get("pass_pause", 10.0))
        start = self._start_for(song)
        self.player.set_playlist(stems, pause, duration=song.duration,
                                 lead=base.lead, start=start)
        self.selected_song = song
        self._applied = applied_key
        self._set_playback_song(song)
        self._show_split_details(song, length, pause)
        if not self._capturing:
            self._set_idle_status()

    def _show_split_details(self, song, length, pause):
        self.now_title.configure(text=song.name)
        total = sum(p.played for p in song.passes)
        keys = " / ".join(key_name(p.key_shift) for p in song.passes)
        bits = [f"{len(song.passes)} passes", f"{total} notes",
                f"{song.bpm:g} BPM", f"{_fmt_length(length)} per pass",
                f"keys {keys}"]
        dropped = sum(p.dropped for p in song.passes)
        if dropped:
            bits.append(f"{dropped} skipped")
        self.details.configure(text="  ·  ".join(bits), foreground=COLOR_IDLE)

    def _show_details(self, song, length=None):
        self.now_title.configure(text=song.name)
        bits = [f"{song.note_count} notes"]
        if song.dropped:
            bits.append(f"{song.dropped} skipped")
        bits.append(f"Key {song.key_name}")
        bits.append(f"{song.bpm:g} BPM")
        bits.append(_fmt_length(length if length is not None
                                else song.duration))
        self.details.configure(text="  ·  ".join(bits), foreground=COLOR_IDLE)

    def _show_empty_library(self):
        self.now_title.configure(text="No song selected")
        self._set_bpm_control(None)
        self._set_playback_song(None)
        self.details.configure(
            text="Add .mid or .midi files to the songs/ folder.",
            foreground=COLOR_IDLE)
        self._set_status("No songs in the songs/ folder", "error")

    # ----------------------------------------------------------------- trigger
    def _trigger_text(self):
        return key_display(self.player.get_trigger())

    def _toggle_capture(self):
        if self.capture_btn is None or not self.capture_btn.winfo_exists():
            return
        if self._capturing:
            self.player.cancel_capture()
            self._end_capture()
            self._set_idle_status()
            return
        self._capturing = True
        self.player.begin_capture()
        self.capture_btn.configure(text="Cancel")
        self._set_status("Press any key to set the trigger…", "capture")

    def _end_capture(self):
        self._capturing = False
        if self.capture_btn is not None and self.capture_btn.winfo_exists():
            self.capture_btn.configure(text="Change…")

    def _captured(self, key):
        self._end_capture()
        reason = reserved_reason(key)
        if reason:
            self._set_status(reason, "error")
            return
        self.player.set_trigger(key)
        if self.trigger_label is not None and self.trigger_label.winfo_exists():
            self.trigger_label.configure(text=key_display(key))
        self._update_trigger_header()
        self._set_status(f"Trigger set to {key_display(key)}", "idle")

    # ------------------------------------------------------------------ status
    def _set_status(self, text, kind):
        color = {"idle": COLOR_IDLE, "playing": COLOR_PLAY,
                 "error": COLOR_ERROR, "capture": COLOR_CAPTURE,
                 "pause": COLOR_PAUSE}[kind]
        self.status_label.configure(text=text, foreground=color)

    def _set_idle_status(self):
        trigger = self._trigger_text()
        if not self.songs:
            self._set_status("No songs in the songs/ folder", "error")
        elif self.selected_song is not None:
            song = self.selected_song
            if song.split:
                count = len(song.passes)
                word = "pass" if count == 1 else "passes"
                first = song.passes[0]
                self._set_status(
                    f"Ready — set key {key_name(first.key_shift)}, then press "
                    f"{trigger} to play {count} {word} of {song.name}", "idle")
            else:
                self._set_status(
                    f"Ready — press {trigger} to play {song.name}", "idle")
        elif self.selected_path is not None:
            self._set_status("The selected song can't be played — see the "
                             "details below.", "error")
        else:
            self._set_status(f"Ready — press {trigger} to play", "idle")

    def _poll(self):
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "state":
                    if payload == "playing":
                        name = self.selected_song.name if self.selected_song else "song"
                        self._set_status(
                            f"▶ Playing {name} — press "
                            f"{self._trigger_text()} to stop", "playing")
                    else:
                        self._pause_deadline = None
                        self._pause_info = None
                        if not self._capturing:
                            self._set_idle_status()
                elif kind == "pass":
                    self._pause_deadline = None
                    self._pause_info = None
                    info = payload.get("info") or {}
                    index = payload.get("index", 0) + 1
                    count = payload.get("count", 1)
                    if count > 1:
                        self._set_status(
                            f"▶ Pass {index}/{count} — game key "
                            f"{info.get('key', '?')}", "playing")
                    else:
                        name = (self.selected_song.name
                                if self.selected_song else "song")
                        self._set_status(
                            f"▶ Playing {name} — press "
                            f"{self._trigger_text()} to stop", "playing")
                elif kind == "pause":
                    self._pause_info = payload
                    self._pause_deadline = (time.perf_counter()
                                            + float(payload.get("seconds", 0)))
                    self._update_pause_status()
                elif kind == "capture":
                    self._captured(payload)
        except queue.Empty:
            pass
        if self._pause_deadline is not None:
            self._update_pause_status()
        self._update_playback_progress()
        self._poll_id = self.root.after(80, self._poll)

    def _update_pause_status(self):
        payload = self._pause_info
        if payload is None or self._pause_deadline is None:
            return
        info = payload.get("info") or {}
        remaining = max(0.0, self._pause_deadline - time.perf_counter())
        next_number = payload.get("next", 0) + 1
        count = payload.get("count", 1)
        self._set_status(
            f"⏸ Set game key to {info.get('key', '?')} for pass "
            f"{next_number}/{count} — starting in {remaining:.0f} s", "pause")

    def _auto_refresh(self):
        self._refresh_songs()
        self._auto_id = self.root.after(int(self.config.get("poll_ms", 1000)),
                                        self._auto_refresh)

    # ------------------------------------------------------------------- close
    def _close(self):
        for attr in ("_poll_id", "_auto_id"):
            after_id = getattr(self, attr, None)
            if after_id is not None:
                try:
                    self.root.after_cancel(after_id)
                except tk.TclError:
                    pass
        if self._preview_resize_id is not None:
            try:
                self.preview.after_cancel(self._preview_resize_id)
            except tk.TclError:
                pass
        self._settings_window = None
        self.player.shutdown()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def _dim_color(hex_color):
    """Blend a part colour toward the background for the 'not played' rows."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    br, bg_, bb = (int(theme.BG[i:i + 2], 16) for i in (1, 3, 5))
    t = 0.40
    return "#%02x%02x%02x" % (int(r + (br - r) * t),
                              int(g + (bg_ - g) * t),
                              int(b + (bb - b) * t))


def _fmt_clock(seconds):
    seconds = max(0.0, seconds)
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _fmt_length(seconds):
    seconds = max(0.0, seconds)
    minutes, secs = divmod(seconds, 60)
    if minutes >= 1:
        return f"{int(minutes)}:{secs:04.1f}"
    return f"{secs:.1f} s"
