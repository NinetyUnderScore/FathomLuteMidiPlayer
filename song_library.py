"""Scan the songs/ folder and work out each song's in-game settings.

Only Standard MIDI files (.mid/.midi) are supported.

Every song needs an in-game "key" (which note the game's 12-note octave
starts on). The game's octave is momentary — hold e for one octave up, q for
one down — so there is no starting-octave setting and no persistent octave
state. The key that leaves the fewest notes out of the game's reach is
chosen automatically.

MIDI songs keep their tracks/instruments separate (see midi_import.Part).
Several parts can be layered in priority order: the highest-priority part
decides the octave and Shift state for each chord, and notes from lower
priority parts that cannot share it are skipped. A three-octave window
(selected in the UI preview) also filters out notes outside the game's range.
CONFIG["top_voice"] collapses the selected parts into one highest-voice line.
CONFIG["song_overrides"] can force the choice per file.

The user's per-song tempo, instrument order and game key live in
SongSettings.json (see song_settings.py) and are applied while loading.
Raising or lowering the tempo scales the note timeline in memory; the MIDI
file is never rewritten.
"""
import os
from dataclasses import dataclass, field

import midi_import
import song_settings
from play_macro import (KEY_SHIFT_MAX, KEY_SHIFT_MIN, NOTE_NAMES, ONSET_EPS,
                        arrange)

SUPPORTED_EXTS = (".mid", ".midi")
# When frozen, PyInstaller unpacks the modules into a temporary folder, so
# anchor songs/ and SongSettings.json to the exe itself.
BASE_DIR = song_settings.app_dir()


@dataclass
class Pass:
    """One register band of a split song, played as its own playthrough.

    groups are on the song's raw timeline (absolute seconds), so every pass
    can be planned against the same origin and the recordings line up when
    layered. key_shift was chosen for this band alone.
    """
    number: int                  # 1-based, for the UI and status messages
    key_shift: int
    groups: list                 # play_macro.chord_groups()
    played: int                  # notes this pass actually plays
    dropped: int                 # notes this pass cannot play
    low: int                     # lowest pitch it plays
    high: int                    # highest pitch it plays
    raw_notes: int = 0           # notes assigned to the band before arranging


@dataclass
class Song:
    path: str
    name: str
    bpm: float
    duration: float
    note_count: int
    key_shift: int | None
    error: str | None = None
    mtime_ns: int = 0
    size: int = 0
    parts: list = field(default_factory=list)
    part_indices: tuple = ()          # priority order, highest first
    groups: list = field(default_factory=list)  # play_macro.chord_groups()
    dropped: int = 0
    window_low: int | None = None     # lowest MIDI pitch the game can play
    top_voice: bool = False           # built from the highest voice only
    passes: list = field(default_factory=list)  # split playthroughs, if any
    timeline_origin: float = 0.0      # raw second that maps to time 0
    unreachable: int = 0              # notes outside every in-game key window
    midi: object | None = field(default=None, repr=False)

    @property
    def ok(self):
        return self.error is None

    @property
    def split(self):
        return bool(self.passes)

    @property
    def key_name(self):
        if self.split:
            return f"{len(self.passes)} passes"
        return "—" if self.key_shift is None else key_name(self.key_shift)

    @property
    def part_name(self):
        if not self.parts:
            return None
        chosen = [self.parts[i] for i in self.part_indices
                  if 0 <= i < len(self.parts)]
        if not chosen:
            return None
        if len(chosen) == 1:
            return chosen[0].name
        if len(chosen) == len(self.parts):
            return f"all {len(chosen)} parts"
        names = [part.name for part in chosen]
        if len(names) > 4:
            return f"{len(names)} parts: " + " → ".join(names[:4]) + " → …"
        return " → ".join(names)


def key_name(key_shift):
    """Note the in-game key setting should be set to (A = default)."""
    return NOTE_NAMES[(9 + key_shift) % 12]


def band_base_low(mid_octave):
    """Lowest pitch of the game's three octaves with the default A key."""
    return 12 * (mid_octave + 1) - 3


def band_bounds(key_shift, mid_octave):
    """(lowest, highest) scientific-pitch names of the playable three octaves."""
    low = band_base_low(mid_octave) + key_shift
    high = low + 35
    return (f"{NOTE_NAMES[low % 12]}{low // 12 - 1}",
            f"{NOTE_NAMES[high % 12]}{high // 12 - 1}")


def window_bounds(key_shift, mid_octave):
    """(lowest, highest) pitches of the middle octave (kept for compatibility)."""
    mid_start = 12 * (mid_octave + 1) + 9 + key_shift
    low = f"{NOTE_NAMES[mid_start % 12]}{mid_start // 12 - 1}"
    high = f"{NOTE_NAMES[(mid_start + 11) % 12]}{(mid_start + 11) // 12 - 1}"
    return low, high


def _equal_bands(low, high, count):
    """Split the inclusive range low..high into count equal-width bands."""
    span = high - low + 1
    if count <= 1 or span <= 0:
        return [(low, high)]
    starts = [low + round(i * span / count) for i in range(count)]
    starts.append(high + 1)
    bands = []
    for i in range(count):
        band_low, band_high = starts[i], starts[i + 1] - 1
        if band_high >= band_low:
            bands.append((band_low, band_high))
    return bands or [(low, high)]


def _edge_bands(low, high, count, where="bottom"):
    """Make count-1 twelve-note bands from an edge, remainder at the other.

    Twelve notes (span 11) fit one game octave, so those passes never split a
    chord across octaves. where="middle" puts twelve-note bands at both edges
    and leaves the remainder in the middle. Used when the song is wider than
    count * 12 notes and at least one band must span an octave boundary.
    """
    if count <= 1:
        return [(low, high)]
    bands = []
    lo, hi = low, high
    if where == "bottom":
        for _ in range(count - 1):
            if lo > hi:
                break
            bands.append((lo, min(lo + 11, hi)))
            lo += 12
    elif where == "top":
        for _ in range(count - 1):
            if hi < lo:
                break
            bands.append((max(hi - 11, lo), hi))
            hi -= 12
    else:  # middle
        for _ in range(count // 2):
            if lo > hi:
                break
            bands.append((lo, min(lo + 11, hi)))
            lo += 12
        for _ in range((count - 1) // 2):
            if hi < lo:
                break
            bands.append((max(hi - 11, lo), hi))
            hi -= 12
    if lo <= hi:
        bands.append((lo, hi))
    bands.sort()
    return bands or [(low, high)]


def _passes_for_bands(tracks, bands, mid_octave, eps):
    """Arrange each pitch band; returns (passes, dropped)."""
    passes = []
    dropped = 0
    for band_low, band_high in bands:
        band_tracks = [
            [(start, end, pitch) for start, end, pitch in track
             if band_low <= pitch <= band_high]
            for track in tracks
        ]
        if not any(band_tracks):
            continue
        try:
            arrangement = arrange(band_tracks, mid_octave, None, eps=eps)
        except ValueError:
            # A band that cannot land anywhere is left out; the remaining
            # passes still play. This should be rare because the band was
            # filtered to the reachable range.
            continue
        pitches = [pitch for group in arrangement.groups
                   for pitch in group["pitches"]]
        if not pitches:
            continue
        dropped += arrangement.dropped
        passes.append(Pass(
            number=0,  # renumbered after the best band layout is chosen
            key_shift=arrangement.key_shift,
            groups=arrangement.groups,
            played=arrangement.played,
            dropped=arrangement.dropped,
            low=min(pitches),
            high=max(pitches),
            raw_notes=sum(len(track) for track in band_tracks),
        ))
    return passes, dropped


def build_passes(tracks, mid_octave, count, eps=ONSET_EPS):
    """Arrange a song as count register-band playthroughs.

    tracks are priority-ordered lists of (start, end, pitch), already
    transposed. Returns (passes, timeline_origin, duration, unreachable).
    Each pass gets its own game key and only the notes in its band, so the
    passes are disjoint and can be layered back into the full song. Within a
    chord, notes that need a different octave or Shift state cannot sound
    together, so splitting by register keeps more of each chord.

    When the playable span fits count * 12 notes, equal-width bands keep every
    pass inside one game octave, so no chord is ever split by an octave
    boundary. Wider songs have to put the excess somewhere, so several band
    layouts are arranged and the one with the fewest dropped notes wins; for
    example two twelve-note bands plus one wide band usually beats three
    equally wide bands that all straddle octave boundaries.
    """
    entries = [(start, end, pitch) for track in tracks for start, end, pitch in track]
    if not entries:
        raise ValueError("No notes to play in the selected parts.")
    origin = min(start for start, _end, _pitch in entries)
    end = max(end for _start, end, _pitch in entries)

    base_low = band_base_low(mid_octave)
    reach_low = base_low + KEY_SHIFT_MIN
    reach_high = base_low + KEY_SHIFT_MAX + 35
    playable = [pitch for _s, _e, pitch in entries
                if reach_low <= pitch <= reach_high]
    unreachable = len(entries) - len(playable)
    if not playable:
        raise ValueError(
            "None of the selected notes are inside the game's three-octave "
            "range. Change the transpose or choose other parts.")

    pmin, pmax = min(playable), max(playable)
    span = pmax - pmin + 1

    if count > 1 and span > 12 * count:
        layouts = [
            _equal_bands(pmin, pmax, count),
            _edge_bands(pmin, pmax, count, "bottom"),
            _edge_bands(pmin, pmax, count, "top"),
            _edge_bands(pmin, pmax, count, "middle"),
        ]
        best = None
        seen = set()
        for bands in layouts:
            key = tuple(bands)
            if key in seen:
                continue
            seen.add(key)
            candidate, cost = _passes_for_bands(
                tracks, bands, mid_octave, eps)
            if candidate and (best is None or cost < best[0]):
                best = (cost, candidate)
        passes = best[1] if best is not None else []
    else:
        passes, _cost = _passes_for_bands(
            tracks, _equal_bands(pmin, pmax, count), mid_octave, eps)

    if not passes:
        raise ValueError("No playable notes left after splitting into passes.")
    for number, pass_ in enumerate(passes, start=1):
        pass_.number = number
    return passes, origin, end - origin, unreachable


class SongLibrary:
    """Lists songs and caches parsed results, reloading only changed files."""

    def __init__(self, config, settings=None):
        self.config = config
        self.settings = (settings if settings is not None
                         else song_settings.SongSettings())
        folder = config.get("songs_dir", "songs")
        self.folder = folder if os.path.isabs(folder) else os.path.join(BASE_DIR, folder)
        self._cache = {}

    def invalidate(self):
        """Forget cached parses, e.g. after the UI changes transpose or mode."""
        self._cache.clear()

    def scan(self):
        """Return (songs, signature). Reuse cached parses for unchanged files."""
        try:
            entries = [e for e in os.scandir(self.folder)
                       if e.is_file() and e.name.lower().endswith(SUPPORTED_EXTS)]
        except FileNotFoundError:
            os.makedirs(self.folder, exist_ok=True)
            entries = []

        entries.sort(key=lambda e: e.name.lower())
        songs = []
        seen = set()
        for entry in entries:
            try:
                stat = entry.stat()
            except OSError:
                continue
            seen.add(entry.path)
            cached = self._cache.get(entry.path)
            if cached and cached.mtime_ns == stat.st_mtime_ns and cached.size == stat.st_size:
                songs.append(cached)
            else:
                song = self._load(entry.path, entry.name, stat)
                self._cache[entry.path] = song
                songs.append(song)

        for path in list(self._cache):
            if path not in seen:
                del self._cache[path]

        signature = tuple((s.path, s.mtime_ns, s.size) for s in songs)
        return songs, signature

    # ------------------------------------------------------------------ config
    def _mid_octave(self):
        return self.config.get("mid_octave", 4)

    def _overrides(self, name):
        return self.config.get("song_overrides", {}).get(name, {})

    def _forced_key(self, name, window_low=None):
        """key_shift to force, or None to let arrange() choose."""
        if window_low is not None:
            return int(window_low) - band_base_low(self._mid_octave())
        value = self._overrides(name).get(
            "key_shift", self.config.get("key_shift", "auto"))
        if isinstance(value, str) and value.strip().lower() in ("", "auto"):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError(f"key_shift must be a number (got {value!r})")

    def _split_count(self):
        """Number of layered playthroughs, or 1 for normal playback."""
        try:
            count = int(self.config.get("split_passes", 1))
        except (TypeError, ValueError):
            return 1
        return max(1, min(count, 8))

    # ------------------------------------------------------------------ loading
    def _check_part(self, index, midi_file):
        if not 0 <= index < len(midi_file.parts):
            raise ValueError(f"part {index} does not exist (this file has "
                             f"{len(midi_file.parts)} part(s))")

    def _initial_parts(self, midi_file, overrides):
        """Priority-ordered tuple of parts to start with."""
        choice = overrides.get("part")
        if choice is None:
            return (midi_file.default_part,)
        if isinstance(choice, str):
            lowered = choice.strip().lower()
            if lowered in ("", "auto"):
                return (midi_file.default_part,)
            if lowered == "all":
                return tuple(range(len(midi_file.parts)))
        if isinstance(choice, (list, tuple, set, frozenset)):
            indices = []
            for item in choice:
                try:
                    index = int(item)
                except (TypeError, ValueError):
                    raise ValueError('part override list must contain numbers '
                                     f"(got {item!r})")
                self._check_part(index, midi_file)
                if index not in indices:
                    indices.append(index)
            if not indices:
                raise ValueError("part override list is empty")
            return tuple(indices)
        try:
            index = int(choice)
        except (TypeError, ValueError):
            raise ValueError('part override must be a number, a list of '
                             'numbers, "auto" or "all" '
                             f"(got {choice!r})")
        self._check_part(index, midi_file)
        return (index,)

    def _load(self, path, name, stat):
        overrides = self._overrides(name)
        saved = self.settings.get(name)
        try:
            midi_file = midi_import.read(path)
            saved_bpm = saved.get("bpm")
            if saved_bpm is not None:
                try:
                    midi_file.set_bpm(float(saved_bpm))
                except (TypeError, ValueError):
                    pass
            parts = midi_file.parts
            # Saved instrument choices (and their priority order) win over
            # CONFIG defaults and overrides.
            part_indices = None
            saved_parts = saved.get("parts")
            if saved_parts:
                part_indices = self._normalise_parts(saved_parts, midi_file)
            if part_indices is None:
                try:
                    part_indices = self._initial_parts(midi_file, overrides)
                except ValueError as exc:
                    # Keep the parts list so the UI can still select parts.
                    return Song(path=path, name=name, bpm=midi_file.bpm,
                                duration=0.0, note_count=0, key_shift=None,
                                error=_friendly(exc),
                                mtime_ns=stat.st_mtime_ns,
                                size=stat.st_size, parts=parts,
                                part_indices=(midi_file.default_part,),
                                midi=midi_file)
            window_low = overrides.get("window_low")
            saved_key = saved.get("key_shift")
            if saved_key is not None:
                try:
                    saved_key = int(saved_key)
                except (TypeError, ValueError):
                    saved_key = None
                if (saved_key is not None
                        and KEY_SHIFT_MIN <= saved_key <= KEY_SHIFT_MAX):
                    window_low = band_base_low(self._mid_octave()) + saved_key
            try:
                forced_key = self._forced_key(name, window_low)
            except ValueError as exc:
                return Song(path=path, name=name, bpm=midi_file.bpm,
                            duration=0.0, note_count=0, key_shift=None,
                            error=_friendly(exc), mtime_ns=stat.st_mtime_ns,
                            size=stat.st_size, parts=parts,
                            part_indices=part_indices, midi=midi_file)
            return self._build_midi(path, name, midi_file, part_indices,
                                    stat.st_mtime_ns, stat.st_size,
                                    forced_key)
        except Exception as exc:  # a broken file should not break the whole list
            return Song(path=path, name=name, bpm=0.0, duration=0.0,
                        note_count=0, key_shift=None, error=_friendly(exc),
                        mtime_ns=stat.st_mtime_ns, size=stat.st_size)

    def _build_midi(self, path, name, midi_file, part_indices, mtime_ns, size,
                    forced_key=None):
        mid_octave = self._mid_octave()
        transpose = self.config.get("transpose", 0)
        top_voice = bool(self.config.get("top_voice", False))
        parts = midi_file.parts
        tracks, last_end = [], 0.0
        try:
            if top_voice:
                # One monophonic line: the highest note STARTING at each
                # onset across every selected part, ignoring priority. Notes
                # are taps (the game cannot hold), so a sustained note never
                # hides a lower note that begins while it is held.
                intervals = [(start, end, pitch + transpose)
                             for start, end, pitch
                             in midi_file.top_voice_intervals(part_indices)]
                tracks.append(intervals)
                for _start, end, _pitch in intervals:
                    last_end = max(last_end, end)
            else:
                for index in part_indices:
                    intervals = [(start, end, pitch + transpose)
                                 for start, end, pitch
                                 in midi_file.part_intervals(index)]
                    tracks.append(intervals)
                    for _start, end, _pitch in intervals:
                        last_end = max(last_end, end)
            if self._split_count() > 1:
                # Register-band playthroughs for layered recording.
                passes, origin, pass_duration, unreachable = build_passes(
                    tracks, mid_octave, self._split_count(),
                    eps=0.0 if top_voice else ONSET_EPS)
                return Song(
                    path=path, name=name,
                    bpm=midi_file.bpm, duration=pass_duration,
                    note_count=sum(p.played for p in passes),
                    key_shift=None, mtime_ns=mtime_ns,
                    size=size, parts=parts, part_indices=part_indices,
                    dropped=sum(p.dropped for p in passes) + unreachable,
                    passes=passes, timeline_origin=origin,
                    unreachable=unreachable, top_voice=top_voice,
                    midi=midi_file)
            arrangement = arrange(
                tracks, mid_octave, forced_key,
                # One voice already: do not merge ornaments into chords.
                eps=0.0 if top_voice else ONSET_EPS)
        except Exception as exc:
            window_low = None
            if (forced_key is not None
                    and KEY_SHIFT_MIN <= forced_key <= KEY_SHIFT_MAX):
                window_low = band_base_low(mid_octave) + forced_key
            return Song(path=path, name=name,
                        bpm=midi_file.bpm, duration=0.0, note_count=0,
                        key_shift=None,
                        error=_friendly(exc), mtime_ns=mtime_ns, size=size,
                        parts=parts, part_indices=part_indices,
                        window_low=window_low, top_voice=top_voice,
                        midi=midi_file)

        origin = arrangement.groups[0]["t"]
        duration = max(last_end - origin, 0.0)
        return Song(path=path, name=name,
                    bpm=midi_file.bpm, duration=duration,
                    note_count=arrangement.played, key_shift=arrangement.key_shift,
                    mtime_ns=mtime_ns,
                    size=size, parts=parts, part_indices=part_indices,
                    groups=arrangement.groups, dropped=arrangement.dropped,
                    window_low=band_base_low(mid_octave) + arrangement.key_shift,
                    top_voice=top_voice, midi=midi_file)

    # ------------------------------------------------------------------ parts
    def _normalise_parts(self, part_indices, midi_file):
        if part_indices is None:
            return tuple(range(len(midi_file.parts)))
        try:
            items = list(part_indices)
        except TypeError:
            return None
        ordered = []
        for item in items:
            try:
                index = int(item)
            except (TypeError, ValueError):
                return None
            if not 0 <= index < len(midi_file.parts):
                return None
            if index not in ordered:
                ordered.append(index)
        return tuple(ordered) if ordered else None

    def with_parts(self, song, part_indices, window_low=None, auto=False):
        """Return a copy of a MIDI song for the chosen parts, in priority order.

        part_indices: ordered iterable (first = highest priority).
        window_low:   lowest pitch of the three-octave window, or None to keep
                      the automatic / configured choice.
        auto:         ignore CONFIG/overrides and let arrange() choose the
                      key window.
        """
        if song.midi is None:
            return song
        ordered = self._normalise_parts(part_indices, song.midi)
        if ordered is None:
            return song
        top_voice = bool(self.config.get("top_voice", False))
        split = self._split_count() > 1
        if (song.ok and ordered == song.part_indices
                and (window_low is None or split)
                and not auto and song.top_voice == top_voice):
            return song
        if auto or split:
            # Split passes choose their own key per register.
            forced_key = None
        else:
            try:
                forced_key = self._forced_key(song.name, window_low)
            except ValueError:
                return song
        rebuilt = self._build_midi(song.path, song.name, song.midi, ordered,
                                   song.mtime_ns, song.size, forced_key)
        # Keep the cache in step so a later scan does not resurrect the old
        # part selection or tempo.
        self._cache[song.path] = rebuilt
        return rebuilt

    def with_bpm(self, song, bpm):
        """Rebuild a song at a new tempo without touching its MIDI file.

        The tempo map is scaled in memory (MidiFile.set_bpm), then the
        arrangement is rebuilt with the same parts and game key, so preview,
        playback and saved settings all stay consistent.
        """
        if song.midi is None:
            return song
        try:
            song.midi.set_bpm(bpm)
        except (TypeError, ValueError):
            return song
        forced_key = None
        if self._split_count() <= 1 and song.key_shift is not None:
            forced_key = song.key_shift
        parts = (song.part_indices
                 or (song.midi.default_part,))
        rebuilt = self._build_midi(song.path, song.name, song.midi, parts,
                                   song.mtime_ns, song.size, forced_key)
        # The cache is shared with later scans; storing the rebuilt song keeps
        # refreshes from resurrecting the old tempo.
        self._cache[song.path] = rebuilt
        return rebuilt


def _friendly(exc):
    text = str(exc) or exc.__class__.__name__
    return text[0].upper() + text[1:] if text else text
