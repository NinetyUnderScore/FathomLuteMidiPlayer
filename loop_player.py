#!/usr/bin/env python3
"""Toggle the loaded song with the trigger key.

This is a library driven by the UI (app_ui.py); there is no command line.

Press the trigger once to start playback and again to stop it; holding it
does nothing extra. The trigger key is not blocked, so the game still
receives it. Pick a key the game does not use. Keys the macro itself presses
(q, e, Shift and the note keys z/x/c/v/b/n/m) are rejected as triggers
because the synthetic presses would feed back into the listener.
"""
import threading
import time

from pynput.keyboard import Controller, Key, KeyCode, Listener

GAME_CHARS = set("zxcvbnmZXCVBNM")
OCTAVE_CHARS = set("qeQE")
MODIFIER_KEYS = {Key.alt, Key.alt_l, Key.alt_r, Key.alt_gr,
                 Key.ctrl, Key.ctrl_l, Key.ctrl_r,
                 Key.shift, Key.shift_l, Key.shift_r}


def key_from_name(name):
    normalized = name.strip().lower().replace(" ", "_").replace("-", "_")
    if hasattr(Key, normalized):
        return getattr(Key, normalized)
    if len(name) == 1:
        return KeyCode.from_char(name)
    raise ValueError(f"Unknown key name: {name!r}")


def key_display(key):
    """Human-readable name, e.g. Page Down, F8, Esc, a."""
    if isinstance(key, KeyCode):
        if key.char == " ":
            return "Space"
        if key.char is not None:
            return key.char
        if key.vk is not None:
            return f"VK {key.vk}"
        return "Unknown key"
    name = getattr(key, "name", None) or str(key)
    return name.replace("_", " ").title()


def reserved_reason(key):
    """Why this key cannot be the trigger, or None if it is fine."""
    if isinstance(key, KeyCode):
        if key.char and key.char in GAME_CHARS:
            return "That key is used to play notes. Choose another one."
        if key.char and key.char in OCTAVE_CHARS:
            return "That key changes the octave. Choose another one."
        return None
    if key in MODIFIER_KEYS:
        return "Modifier keys cannot be the trigger. Choose another one."
    return None


def keys_equal(a, b):
    if a == b:
        return True
    if isinstance(a, KeyCode) and isinstance(b, KeyCode):
        if a.char is not None or b.char is not None:
            return a.char == b.char
        return a.vk == b.vk
    return False


def _is_modifier_key(key):
    """True for the planner's q/e octave keys and Shift."""
    return key in ("q", "e") or not isinstance(key, str)


class LoopPlayer:
    """Global listener + loop thread.

    messages is a queue.Queue; this class only ever puts plain tuples in it:
        ("state", "playing" | "stopped")
        ("capture", key)
        ("pass", {"index", "count", "info"})
        ("pause", {"next", "count", "info", "seconds"})
    so the UI can consume them safely on the Tk thread.
    """

    def __init__(self, config, messages):
        self.config = config
        self.messages = messages
        self._lock = threading.RLock()
        self._trigger = key_from_name(config.get("trigger_key", "page_down"))
        self._capture = False
        self._song = None
        self._stop = threading.Event()
        self._worker = None
        self._listener = None
        self._keyboard = Controller()
        self._trigger_down = False
        # (wall_clock_start, song_offset, lead, song_duration) while playing.
        self._position = None

    # ---------------------------------------------------------------- lifecycle
    def start(self):
        self._listener = Listener(on_press=self._on_press,
                                  on_release=self._on_release)
        self._listener.daemon = True
        self._listener.start()

    def shutdown(self):
        self._stop.set()
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self._halt()

    def _halt(self):
        # Stop and grab the worker under one lock, so a trigger press that
        # arrives mid-switch cannot start the song we are replacing.
        with self._lock:
            self._stop.set()
            worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(1.0)

    # ------------------------------------------------------------------- songs
    def set_song(self, events, length, duration=None, lead=0.0, start=0.0):
        """Normal playback: one pass, looped until stopped."""
        self.set_playlist([{"events": events, "length": length}], 0.0,
                          duration=duration, lead=lead, start=start)

    def set_playlist(self, items, pause=0.0, duration=None, lead=0.0,
                     start=0.0):
        """Play several passes in order, pausing `pause` seconds between them.

        Each item is {"events", "length", "info"} plus optional "duration",
        "lead" and "start" (song seconds). The events already hold every q/e
        press they need, so the player keeps no octave state. `start` is where
        in the song playback begins; it is trimmed at play time so changing it
        does not need a replan. With pause=0 the sequence still loops, which
        is the normal single-pass behavior.
        """
        normalized = []
        for item in items:
            item = dict(item)
            item.setdefault("duration", duration if duration is not None
                            else item.get("length", 0.0))
            item.setdefault("lead", lead)
            item["start"] = max(0.0, float(item.get("start", start)))
            normalized.append(item)
        with self._lock:
            self._stop.set()
            worker = self._worker
            self._song = {"items": normalized, "pause": float(pause)}
            self._position = None
        if worker is not None and worker.is_alive():
            worker.join(1.0)

    def set_start(self, seconds):
        """Choose where the next playback starts, stopping the current one."""
        seconds = max(0.0, float(seconds))
        with self._lock:
            self._stop.set()
            if self._song is not None:
                for item in self._song["items"]:
                    item["start"] = seconds
            self._position = None
            worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(1.0)

    def stop(self):
        """Stop playback (the worker exits on its own)."""
        with self._lock:
            self._stop.set()

    def is_playing(self):
        with self._lock:
            return (self._worker is not None and self._worker.is_alive()
                    and not self._stop.is_set())

    def position(self):
        """Current song position in seconds, or None when not playing."""
        with self._lock:
            state = self._position
        if state is None:
            return None
        wall_start, offset, lead, duration = state
        elapsed = time.perf_counter() - wall_start
        value = offset + max(0.0, elapsed - lead)
        return max(0.0, min(value, duration))

    def clear_song(self):
        with self._lock:
            self._stop.set()
            worker = self._worker
            self._song = None
            self._position = None
        if worker is not None and worker.is_alive():
            worker.join(1.0)

    # ----------------------------------------------------------------- trigger
    def begin_capture(self):
        with self._lock:
            self._stop.set()
            worker = self._worker
            self._capture = True
        if worker is not None and worker.is_alive():
            worker.join(1.0)

    def cancel_capture(self):
        with self._lock:
            self._capture = False

    def set_trigger(self, key):
        with self._lock:
            self._stop.set()
            worker = self._worker
            self._trigger = key
        if worker is not None and worker.is_alive():
            worker.join(1.0)

    def get_trigger(self):
        with self._lock:
            return self._trigger

    # ---------------------------------------------------------------- listener
    def _on_press(self, key):
        with self._lock:
            if self._capture:
                self._capture = False
                self.messages.put(("capture", key))
                return
            trigger = self._trigger
            if not keys_equal(key, trigger):
                return
            if self._trigger_down:
                return  # auto-repeat while the trigger is held
            self._trigger_down = True
            if self._worker is not None and self._worker.is_alive():
                self._stop.set()   # toggle: a press while playing stops
                return
            song = self._song
            if song is None:
                return
            self._stop.clear()
            self._worker = threading.Thread(target=self._play, args=(song,),
                                            daemon=True)
            worker = self._worker
        worker.start()

    def _on_release(self, key):
        with self._lock:
            if keys_equal(key, self._trigger):
                self._trigger_down = False

    # -------------------------------------------------------------------- loop
    def _wait(self, target):
        while not self._stop.is_set():
            left = target - time.perf_counter()
            if left <= 0:
                return True
            time.sleep(min(left - 0.002, 0.01) if left > 0.004 else 0)
        return False

    @staticmethod
    def _prepare_clip(item):
        """Return (events, lead, offset, duration, clip_length) for one pass.

        Events are shifted so the clip starts at time 0. When the start is
        mid-song, any q/e/Shift the planner had held across the cut is pressed
        again at the start so the first note keeps its octave; note keys are
        not re-pressed.
        """
        events = item["events"]
        lead = float(item.get("lead", 0.0))
        start = max(0.0, float(item.get("start", 0.0)))
        duration = float(item.get("duration", item.get("length", 0.0)))
        if start <= 0.0:
            return events, lead, 0.0, duration, float(item["length"])

        t0 = lead + start
        held, kept = set(), []
        for t, _seq, action, key in events:
            if t < t0:
                if action == "down":
                    held.add(key)
                else:
                    held.discard(key)
            else:
                kept.append((t - t0, action, key))
        modifiers = [key for key in held if _is_modifier_key(key)]
        if not kept:
            return [], 0.0, start, duration, 0.02
        out = [(0.0, i, "down", key) for i, key in enumerate(modifiers)]
        base = len(modifiers)
        out.extend((t, base + i, action, key)
                   for i, (t, action, key) in enumerate(kept))
        length = max(duration - start, kept[-1][0] + 0.02)
        return out, 0.0, start, duration, length

    def _play_item(self, clip, start, wait_end):
        """Play one prepared pass and return True if it finished naturally.

        wait_end makes a split pass hold its full length, so the next pass
        (and therefore the next recording) begins at the same point on the
        song timeline. There is no octave state to restore: the game's octave
        only lasts while the event stream holds q/e down.
        """
        events, lead, offset, duration, length = clip
        down = set()
        keyboard = self._keyboard
        with self._lock:
            self._position = (start, offset, lead, duration)

        def send(action, key):
            if action == "down":
                keyboard.press(key)
                down.add(key)
            else:
                keyboard.release(key)
                down.discard(key)

        try:
            if not events:
                # A start point in a trailing rest: hold the clip open
                # instead of spinning the loop.
                return self._wait(start + length)
            for t, _seq, action, key in events:
                if not self._wait(start + t):
                    return False
                send(action, key)
            if wait_end and not self._wait(start + length):
                return False
            return True
        finally:
            for key in list(down):   # never leave keys stuck
                keyboard.release(key)
            with self._lock:
                self._position = None

    def _play(self, song):
        items = song["items"]
        pause = song["pause"]
        if not items:
            return
        single = len(items) == 1 and pause <= 0
        announce = len(items) > 1 or any(item.get("info") for item in items)
        clips = [self._prepare_clip(item) for item in items]
        if not single:
            shared = max(clip[4] for clip in clips)
            clips = [(c[0], c[1], c[2], c[3], shared) for c in clips]

        self.messages.put(("state", "playing"))
        loop_start = time.perf_counter()
        try:
            while not self._stop.is_set():
                for index, item in enumerate(items):
                    if self._stop.is_set():
                        return
                    if announce:
                        self.messages.put(("pass", {
                            "index": index,
                            "count": len(items),
                            "info": item.get("info"),
                        }))
                    start = loop_start if single else time.perf_counter()
                    if not self._play_item(clips[index], start,
                                           wait_end=not single):
                        return
                    if single:
                        loop_start += clips[index][4]  # fixed period: no drift
                        now = time.perf_counter()
                        if now > loop_start:  # resync instead of bursting
                            loop_start = now
                        break
                    if pause > 0:
                        next_index = (index + 1) % len(items)
                        self.messages.put(("pause", {
                            "next": next_index,
                            "count": len(items),
                            "info": items[next_index].get("info"),
                            "seconds": pause,
                        }))
                        if not self._wait(time.perf_counter() + pause):
                            return
        finally:
            with self._lock:
                self._position = None
            self.messages.put(("state", "stopped"))
