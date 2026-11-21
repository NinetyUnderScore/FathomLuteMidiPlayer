#!/usr/bin/env python3
"""Turn MIDI note groups into timed keypress events.

This is a library used by the UI (run.py / app_ui.py). There is no command
line interface; every setting lives in run.py. Only Standard MIDI files are
supported.

GAME LAYOUT (one game octave runs A -> G#):
    A  A# B  C  C# D  D# E  F  F# G  G#
    z  Z  x  X  c  v  V  b  B  n  N  m      (capitals = hold Shift)
    The game's octave is momentary: holding e raises it one octave and
    holding q lowers it one, only while the key is held. With neither held
    the game is in its middle octave. There is no persistent octave state to
    track, so nothing has to be restored between songs.

arrange() and plan_groups(): several notes may sound together, but only when
they need the same Shift state and the same octave at that instant. Notes
that conflict with the highest-priority part are skipped, as are notes
outside the chosen three-octave window. Notes are tapped briefly; holding a
key has no effect. q/e is pressed just before the note and released just
after it; because notes are taps in the game, those margins never lengthen a
note.
"""
from dataclasses import dataclass

# index 0 = A ... 11 = G#   ->  (key, needs_shift)
KEYMAP = [("z", False), ("z", True), ("x", False), ("x", True), ("c", False),
          ("v", False), ("v", True), ("b", False), ("b", True), ("n", False),
          ("n", True), ("m", False)]
# (the capital letters in the game just mean "letter + Shift")

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
SHIFT_INDICES = {1, 3, 6, 8, 10}  # pitch classes inside a window that need Shift
ONSET_EPS = 0.035                 # notes this close start as one chord
                                  # (MIDI chords are often humanized/staggered)

# The in-game key selector runs C, C#, D, ..., A#, B. With the default A4
# in the mid octave, those windows start at C4, C#4, ..., B4. Relative to A
# that is C=-9 ... G#=-1, A=0, A#=1, B=2. Key C is the LOWEST and key B the
# HIGHEST; measured in-game, key C sounds below key G#.
KEY_SHIFT_MIN = -9
KEY_SHIFT_MAX = 2
KEY_SHIFTS = tuple(range(KEY_SHIFT_MIN, KEY_SHIFT_MAX + 1))


@dataclass
class Options:
    """Everything the planner needs. Built from CONFIG in run.py."""
    mid_octave: int = 4       # scientific octave whose A starts the game's mid octave
    key_shift: int = 0        # in-game key setting: semitones the A window is slid by
    hold: float = 0.05
    lead: float = 0.15
    # How long q/e/Shift is held before the note key goes down, and how long
    # it stays down after the note key comes up. Notes are taps, so this only
    # gives the game time to see the modifier; it never lengthens a note.
    # A few milliseconds is enough for an event-driven game, but frame-polled
    # games want at least one frame (16 ms at 60 fps), so keep a margin.
    modifier_lead: float = 0.03
    modifier_tail: float = 0.01


# The game's octave modifiers: q lowers one octave, e raises one. They are
# momentary: the octave changes only while the key is held.
OCTAVE_KEYS = {"q", "e"}


def _is_note_key(key):
    """True for the game's z..m note keys (not q/e or a modifier object)."""
    return isinstance(key, str) and len(key) == 1 and key not in OCTAVE_KEYS


# ---------------------------------------------------------------------------
# Polyphonic MIDI arrangements
#
# The game can sound several notes at once, but every note being played at a
# moment has to need the same octave and the same Shift state. Notes that
# collide with the highest-priority instrument's choice are dropped.
# ---------------------------------------------------------------------------

@dataclass
class Arrangement:
    key_shift: int
    groups: list     # see chord_groups()
    played: int      # notes kept
    dropped: int     # notes skipped (outside the window or a priority clash)


def onset_chunks(tracks, eps=ONSET_EPS):
    """Pre-sort notes into simultaneous-onset chunks.

    Returns [(start_seconds, [(rank, pitch), ...]), ...] in time order. This
    is window-independent, so arrange() computes it once for all 12 keys.
    """
    entries = []
    for rank, intervals in enumerate(tracks):
        for start, _end, pitch in intervals:
            entries.append((start, rank, pitch))
    entries.sort(key=lambda e: (e[0], e[1], -e[2]))

    chunks = []
    i = 0
    while i < len(entries):
        start = entries[i][0]
        j = i + 1
        while j < len(entries) and entries[j][0] - start <= eps:
            j += 1
        chunks.append((start, [(rank, pitch) for _t, rank, pitch
                               in entries[i:j]]))
        i = j
    return chunks


def chord_groups(tracks, mid_start, eps=ONSET_EPS, chunks=None):
    """Group simultaneous notes and enforce one octave / one Shift state.

    tracks: priority-ordered lists of (start_seconds, end_seconds, pitch).
    Returns (groups, dropped_by_rank, dropped_hist_by_rank). A group is
        {"t": seconds, "off": -1..1, "shift": bool, "pitches": [..]}
    dropped_by_rank counts skipped notes per track (highest priority first).
    dropped_hist_by_rank is a per-track count of skipped notes by MIDI pitch
    (index 0..127), used to prefer windows that sacrifice the lowest notes.
    Notes outside the game's three octaves or in a competing octave/Shift
    group are skipped; the highest voice of the highest-priority track always
    decides the group.
    """
    if chunks is None:
        chunks = onset_chunks(tracks, eps)

    groups = []
    dropped = [0] * len(tracks)
    hist = [[0] * 128 for _ in tracks]

    def skip(rank, pitch):
        dropped[rank] += 1
        hist[rank][min(max(pitch, 0), 127)] += 1

    for start, chunk in chunks:
        candidates = []
        for rank, pitch in chunk:
            off, idx = divmod(pitch - mid_start, 12)
            if off < -1 or off > 1:
                skip(rank, pitch)
                continue
            candidates.append((rank, -pitch, pitch, off, idx in SHIFT_INDICES))
        if not candidates:
            continue
        # The highest-priority instrument decides the octave and Shift state;
        # within that instrument the highest voice wins the choice.
        _rank, _neg, _pitch, off, shift = min(candidates,
                                              key=lambda c: (c[0], c[1]))
        for candidate in candidates:
            if candidate[3] != off or candidate[4] != shift:
                skip(candidate[0], candidate[2])
        chosen = [c for c in candidates if c[3] == off and c[4] == shift]
        groups.append({
            "t": start,
            "off": off,
            "shift": shift,
            "pitches": sorted({c[2] for c in chosen}),
            "notes": [(c[0], c[2]) for c in chosen],
        })
    return groups, tuple(dropped), tuple(tuple(h) for h in hist)


def arrange(tracks, mid_octave, forced_key=None, eps=ONSET_EPS):
    """Choose the in-game key window and pre-group the notes.

    Tries every in-game key setting (C..B, i.e. -9..+2 semitones from A).
    Instrument priority is the outer order; within each instrument it keeps
    the highest notes before lower ones (pitch 127 is compared before 126,
    and so on), so a high voice is never traded for more lower notes.

    Octave changes are free and consistent with the momentary q/e modifiers,
    so the number of them a song needs is never part of the cost. A key
    costs only the notes outside its three-octave window plus the notes a
    chord cannot share (a different octave or Shift state at the same
    instant). When two keys play the same notes, distance from the default A
    key only breaks the tie; it is a preference, not an octave-change cost.

    eps is the onset merge window; pass 0 for a source that is already a
    single voice (the "highest voice only" mode) so ornaments stay separate
    notes instead of becoming chords.
    """
    if forced_key is not None:
        try:
            forced_key = int(forced_key)
        except (TypeError, ValueError):
            raise ValueError(f"key_shift must be a number (got {forced_key!r})")
        if not KEY_SHIFT_MIN <= forced_key <= KEY_SHIFT_MAX:
            raise ValueError(f"key_shift must be {KEY_SHIFT_MIN}.."
                             f"{KEY_SHIFT_MAX} (got {forced_key})")

    base = 12 * (mid_octave + 1) + 9
    shifts = [forced_key] if forced_key is not None else KEY_SHIFTS
    chunks = onset_chunks(tracks, eps)
    best = None
    for shift in shifts:
        groups, dropped_by_rank, dropped_hist = chord_groups(
            tracks, base + shift, chunks=chunks)
        if not groups:
            continue
        played = sum(len(group["pitches"]) for group in groups)
        # Instrument priority is the outer order. Within one instrument,
        # prefer dropping the lowest notes: pitch 127 is compared before
        # 126, etc. A higher voice is therefore never sacrificed to save
        # any number of lower notes.
        cost = ()
        for rank in range(len(tracks)):
            hist = dropped_hist[rank]
            cost += tuple(hist[pitch] for pitch in range(127, -1, -1))
        cost += (abs(shift),)
        if best is None or cost < best[0]:
            best = (cost, shift, groups, played, sum(dropped_by_rank))

    if best is None:
        raise ValueError(
            "No notes land inside the game's three-octave range. Drag the "
            "range in the preview or choose different parts.")
    _cost, shift, groups, played, dropped = best
    return Arrangement(shift, groups, played, dropped)


def plan_groups(groups, options, origin=None):
    """Turn arranged onset groups into keypress events.

    Chords are pressed together; the game allows several keys at once as long
    as they share one octave and one Shift state (already guaranteed by
    chord_groups). A chord in the high octave holds e while its keys are
    down, a chord in the low octave holds q, and the middle octave uses
    neither; the modifier is released as soon as the chord ends.

    origin is the raw timeline position that maps to time 0 (default: this
    source's first chord). Split playthroughs pass the song's common origin
    so all passes share one timeline.

    Returns (events, stats) where stats has the number of chords.
    """
    try:
        from pynput.keyboard import Key
    except ImportError:  # lets metadata be inspected without pynput installed
        class Key:
            shift = "shift"

    if not groups:
        raise ValueError("No notes to play in the selected range.")
    if origin is None:
        origin = groups[0]["t"]

    events, seq = [], 0

    def add(time_, act, key):
        nonlocal seq
        events.append((time_, seq, act, key))
        seq += 1

    held = {}  # key -> (down_time, release_deadline)
    last_release = 0.0

    def release_held(before):
        # Note keys before their modifiers: the release of a key does not
        # make a sound, but this keeps the modifier held for the whole note.
        # anchor is the earliest the key may come up (the note it belongs
        # to), so a very close next chord cannot release a modifier before
        # its own note has sounded.
        nonlocal last_release
        keys = sorted(held, key=lambda k: not _is_note_key(k))
        latest = 0.0
        for key in keys:
            anchor, deadline = held[key]
            # Note keys keep a minimum 1 ms tap; modifiers only need to come
            # up no earlier than their own chord, which lets a chord only a
            # fraction of a millisecond later start with the right state.
            pad = 0.001 if _is_note_key(key) else 0.0
            when = max(anchor + pad, min(deadline, before))
            add(when, "up", key)
            latest = max(latest, when)
        held.clear()
        last_release = max(last_release, latest)

    mid_start = 12 * (options.mid_octave + 1) + 9 + options.key_shift
    for group in groups:
        t = options.lead + group["t"] - origin
        # Notes are tapped, so nothing carries over from the previous chord.
        release_held(t - 0.006)

        letters = [KEYMAP[(pitch - mid_start) % 12][0]
                   for pitch in group["pitches"]]

        mods = []
        if group["off"] > 0:
            mods.append("e")
        elif group["off"] < 0:
            mods.append("q")
        if letters and group["shift"]:
            mods.append(Key.shift)
        if mods:
            # Lead the note, but never the previous chord: pressing an octave
            # while the previous key is still down could retune that note.
            # Never let a dense previous chord push the modifier past this
            # note either — the note must have it already.
            mod_down = max(t - options.modifier_lead, last_release + 0.001)
            mod_down = min(mod_down, t)
            deadline = t + options.hold + options.modifier_tail
            for mod in mods:
                add(mod_down, "down", mod)
                # Anchor the release at the chord's own time (not the earlier
                # modifier press) so it stays held across this chord's notes
                # even when the next chord is only a few ms away.
                held[mod] = (t, deadline)
        for letter in letters:
            add(t, "down", letter)
            held[letter] = (t, t + options.hold)

    release_held(float("inf"))
    events.sort(key=lambda e: (e[0], e[1]))
    return events, {"chords": len(groups)}
