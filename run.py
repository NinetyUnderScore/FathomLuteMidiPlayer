#!/usr/bin/env python3
"""Start the Lute song looper.

    pip install pynput
    python run.py

Every setting lives in CONFIG below. Songs are read from the songs/ folder;
add or remove .mid / .midi files while the window is open and the list
updates itself. Only Standard MIDI files are supported.

Per-song choices made in the UI (tempo, instrument priority, game key) are
stored in SongSettings.json in this folder (beside Lute.exe when built).

To build a standalone Lute.exe instead, run `python build.py` (needs
`pip install pyinstaller`). The exe reads songs/ from the folder beside it.
"""

CONFIG = {
    # ---- files ---------------------------------------------------------------
    # Folder (relative to this file) that the song list watches.
    "songs_dir": "songs",

    # Key held down to loop the highlighted song. The UI can change this live.
    # Use pynput names ("page_down", "f8", "end", ...) or a single character.
    "trigger_key": "page_down",

    # ---- game calibration ----------------------------------------------------
    # Scientific octave whose A is the game's "mid" octave. With 4, mid is
    # A4..G#5 when the in-game key is the default A.
    "mid_octave": 4,

    # In-game key window, in semitones relative to A. The key selector runs
    # C, C#, D, ... A#, B, so relative to A: C = -9, C# = -8, ... G# = -1,
    # A = 0, A# = 1, B = 2. C is the lowest key and B the highest. "auto"
    # picks the key that leaves the fewest notes outside the game's three
    # octaves, keeping the highest notes of the highest-priority part first;
    # octave changes are free and never part of the choice. A key saved by
    # the UI in SongSettings.json takes precedence. The preview lets you drag
    # that window instead.
    "key_shift": "auto",

    # Shift every song by N semitones (e.g. -2 to sing lower). The UI has the
    # same control, which is usually handier.
    "transpose": 0,

    # ---- layered recording (split playthroughs) ------------------------------
    # 1 = normal playback. 2-4 splits the song into that many playthroughs
    # by pitch register. Each pass gets its own game key and plays only its
    # own notes; the passes share one timeline, so record each pass and layer
    # the recordings. The UI has the same controls.
    "split_passes": 1,
    # Seconds to wait between passes so you can change the game's key. The
    # status bar counts down and names the next pass's key.
    "pass_pause": 10.0,

    # Play one line only: the highest note starting at each onset across
    # every selected MIDI part, ignoring instrument priority. Every note is
    # a tap, so a held note does not hide lower notes that start beneath it.
    # The UI toggle starts on this value.
    "top_voice": False,

    # ---- note playback -------------------------------------------------------
    # The game's octave is momentary: e raises it one octave, q lowers it
    # one, and releasing the key restores the middle octave. The macro holds
    # the modifier for exactly as long as the notes that need it.
    "hold": 0.05,         # seconds each note key is held
    "lead": 0.15,         # padding before the first note of each loop
    # How long q/e/Shift is held before the note key and after it comes up.
    # Notes are taps, so these margins do not change the note length; they
    # give the game time to register the modifier (a frame-polled game may
    # need 16 ms or more). Raise modifier_lead if low/high notes are silent.
    "modifier_lead": 0.03,
    "modifier_tail": 0.01,

    # ---- UI ------------------------------------------------------------------
    "poll_ms": 1000,      # how often to look for added/removed song files

    # Per-song overrides, by file name. Used when the song has no choice
    # saved in SongSettings.json. MIDI files default to a guessed melody
    # part; the UI also has the "MIDI parts" list, and "part" sets the
    # default selection.
    #   "part": one 0-based index, a list of them in priority order (first =
    #           most important), "all", or "auto".
    #   "window_low": lowest MIDI pitch of the three-octave window (60 = C4);
    #                 pins the filter instead of using key_shift/auto.
    # Example:
    #   "song_overrides": {
    #       "arrangement.mid": {"part": [0, 2], "window_low": 57},
    #       "other.mid": {"key_shift": -1},
    #   },
    "song_overrides": {},
}


def main():
    import tkinter as tk
    from tkinter import messagebox

    try:
        import pynput  # noqa: F401
    except ImportError:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Missing dependency",
            "This program needs the pynput package.\n\n"
            "Open a terminal and run:\n\n    pip install pynput")
        root.destroy()
        return

    from app_ui import App

    App(CONFIG).run()


if __name__ == "__main__":
    main()
