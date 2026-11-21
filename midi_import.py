"""Read a Standard MIDI File (.mid / .midi) into parts and note intervals.

The file is kept structured: every (track, MIDI channel) pair becomes a Part
with its track name, General MIDI instrument, channel, pitch range and note
count. The UI lists these so the user can layer instruments in priority order.

`part_intervals()` returns a part's notes on the song's real timeline, which
the arrangement planner uses to play chords and the preview uses to draw the
instruments. `top_voice_intervals()` keeps the highest note *starting* at
each onset (notes are taps, so a held note never masks a later one).
`set_bpm()` scales that timeline for a tempo override (the file is never
rewritten). `melody()` is the older single-line reduction (highest sounding
note at every moment).

No third-party package is needed; this is a small SMF parser.
"""
from bisect import bisect_right
from dataclasses import dataclass, field

DEFAULT_TEMPO = 500_000  # microseconds per quarter note (120 BPM)
DRUM_CHANNEL = 9
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# General MIDI program names (program numbers 0..127). Used only for display.
GM_INSTRUMENTS = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano",
    "Honky-tonk Piano", "Electric Piano 1", "Electric Piano 2", "Harpsichord",
    "Clavinet", "Celesta", "Glockenspiel", "Music Box", "Vibraphone", "Marimba",
    "Xylophone", "Tubular Bells", "Dulcimer", "Drawbar Organ",
    "Percussive Organ", "Rock Organ", "Church Organ", "Reed Organ", "Accordion",
    "Harmonica", "Tango Accordion", "Acoustic Guitar (nylon)",
    "Acoustic Guitar (steel)", "Electric Guitar (jazz)",
    "Electric Guitar (clean)", "Electric Guitar (muted)", "Overdriven Guitar",
    "Distortion Guitar", "Guitar Harmonics", "Acoustic Bass",
    "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass",
    "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2", "Violin",
    "Viola", "Cello", "Contrabass", "Tremolo Strings", "Pizzicato Strings",
    "Orchestral Harp", "Timpani", "String Ensemble 1", "String Ensemble 2",
    "SynthStrings 1", "SynthStrings 2", "Choir Aahs", "Voice Oohs",
    "Synth Voice", "Orchestra Hit", "Trumpet", "Trombone", "Tuba",
    "Muted Trumpet", "French Horn", "Brass Section", "SynthBrass 1",
    "SynthBrass 2", "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax",
    "Oboe", "English Horn", "Bassoon", "Clarinet", "Piccolo", "Flute",
    "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)",
    "Lead 4 (chiff)", "Lead 5 (charang)", "Lead 6 (voice)", "Lead 7 (fifths)",
    "Lead 8 (bass + lead)", "Pad 1 (new age)", "Pad 2 (warm)",
    "Pad 3 (polysynth)", "Pad 4 (choir)", "Pad 5 (bowed)", "Pad 6 (metallic)",
    "Pad 7 (halo)", "Pad 8 (sweep)", "FX 1 (rain)", "FX 2 (soundtrack)",
    "FX 3 (crystal)", "FX 4 (atmosphere)", "FX 5 (brightness)",
    "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)", "Sitar", "Banjo",
    "Shamisen", "Koto", "Kalimba", "Bag pipe", "Fiddle", "Shanai",
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock", "Taiko Drum",
    "Melodic Tom", "Synth Drum", "Reverse Cymbal", "Guitar Fret Noise",
    "Breath Noise", "Seashore", "Bird Tweet", "Telephone Ring", "Helicopter",
    "Applause", "Gunshot",
]


@dataclass
class Part:
    """One instrument in a MIDI file: a track/channel pair with notes."""
    index: int
    track: int
    channel: int
    track_name: str
    program: int | None
    instrument: str | None
    note_count: int
    low_pitch: int
    high_pitch: int
    median_pitch: float
    intervals: list = field(repr=False)

    @property
    def name(self):
        if self.track_name and self.instrument:
            if self.instrument.casefold() in self.track_name.casefold():
                return self.track_name
            return f"{self.track_name} ({self.instrument})"
        return self.track_name or self.instrument or f"Channel {self.channel + 1}"

    @property
    def range_text(self):
        return (f"{NOTE_NAMES[self.low_pitch % 12]}{self.low_pitch // 12 - 1}"
                f"–{NOTE_NAMES[self.high_pitch % 12]}{self.high_pitch // 12 - 1}")

    @property
    def option_text(self):
        note_word = "note" if self.note_count == 1 else "notes"
        return (f"{self.name} · ch {self.channel + 1} · "
                f"{self.note_count} {note_word} · {self.range_text}")


@dataclass
class MidiFile:
    """Parsed MIDI data with its parts kept separate."""
    path: str
    ticks_per_beat: int
    tempo_events: list
    bpm: float                    # effective tempo (after a BPM override)
    parts: list
    default_part: int | None
    base_bpm: float = 0.0         # tempo as written in the file
    time_scale: float = 1.0       # every second is multiplied by this

    def set_bpm(self, bpm):
        """Scale the whole timeline to a new (initial) tempo.

        The MIDI file is not touched; the tempo map is kept, so relative
        changes still play. `base_bpm` is the file's own initial tempo.
        """
        bpm = float(bpm)
        if not bpm > 0:
            raise ValueError("BPM must be greater than zero")
        base = self.base_bpm or self.bpm
        self.time_scale = base / bpm
        self.bpm = bpm

    def part_intervals(self, part_index):
        """[(start_seconds, end_seconds, pitch), ...] for one part."""
        if not 0 <= part_index < len(self.parts):
            raise ValueError(f"MIDI part {part_index} does not exist")
        converter = getattr(self, "_converter", None)
        if converter is None:
            converter = _tempo_converter(self.tempo_events, self.ticks_per_beat)
            self._converter = converter
        to_seconds, _initial = converter
        scale = self.time_scale
        return [(to_seconds(start) * scale, to_seconds(end) * scale, pitch)
                for start, end, pitch in self.parts[part_index].intervals]

    def top_voice_intervals(self, part_indices=None):
        """Highest note starting at each onset across the selected parts.

        The game's instrument only taps notes and cannot hold them, so every
        note is treated as an instant: a long held note must not mask lower
        notes that start while it is down. Each onset contributes exactly one
        pitch, the highest that begins then. The note's own end is kept only
        as a length hint (for the song's tail); it never decides what sounds.

        Used by the "highest voice only" mode, which ignores instrument
        priority.
        """
        if part_indices is None:
            indices = range(len(self.parts))
        else:
            indices = sorted({int(i) for i in part_indices})
        intervals = []
        for index in indices:
            intervals.extend(self.part_intervals(index))
        return _top_voice_onsets(intervals)

    def melody(self, part_indices=None):
        """Notes for the selected parts (None = every part), top voice only."""
        if part_indices is None:
            intervals = [iv for part in self.parts for iv in part.intervals]
        else:
            if isinstance(part_indices, int):
                part_indices = (part_indices,)
            indices = sorted({int(i) for i in part_indices})
            if not indices:
                raise ValueError("No MIDI parts selected")
            for index in indices:
                if not 0 <= index < len(self.parts):
                    raise ValueError(f"MIDI part {index} does not exist")
            intervals = [iv for index in indices
                         for iv in self.parts[index].intervals]
        return _to_notes(intervals, self.tempo_events, self.ticks_per_beat,
                         self.time_scale)


def _read_vlq(data, pos):
    value = 0
    while True:
        if pos >= len(data):
            raise ValueError("Unexpected end of MIDI data")
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos


def _read_header(data):
    if len(data) < 14 or data[:4] != b"MThd":
        raise ValueError("Not a MIDI file (missing MThd header)")
    header_len = int.from_bytes(data[4:8], "big")
    if header_len < 6:
        raise ValueError("Malformed MIDI header")
    division = int.from_bytes(data[12:14], "big")
    if division & 0x8000:
        raise ValueError("SMPTE time division is not supported")
    if division == 0:
        raise ValueError("MIDI file has no timing resolution")
    pos = 8 + header_len

    tracks = []
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        length = int.from_bytes(data[pos + 4:pos + 8], "big")
        payload = data[pos + 8:pos + 8 + length]
        pos += 8 + length
        if chunk_id == b"MTrk":
            tracks.append(payload)
    if not tracks:
        raise ValueError("MIDI file contains no tracks")
    return tracks, division


def _read_track(track, order_start, track_index):
    """Return (events, order_end, last_tick, track_name, meta_instrument).

    events: (tick, order, kind, payload)
      kind 'on'/'off' payload (track, channel, pitch)
      kind 'program' payload (track, channel, program)
      kind 'tempo'   payload microseconds per quarter note
    """
    events = []
    pos, tick, running = 0, 0, None
    order = order_start
    track_name = ""
    meta_instrument = ""
    while pos < len(track):
        delta, pos = _read_vlq(track, pos)
        tick += delta
        if pos >= len(track):
            break
        status = track[pos]
        if status & 0x80:
            pos += 1
        elif running is None:
            raise ValueError("Malformed MIDI track (bad running status)")
        else:
            status = running

        if status == 0xFF:  # meta event
            if pos >= len(track):
                break
            meta_type = track[pos]
            pos += 1
            length, pos = _read_vlq(track, pos)
            payload = track[pos:pos + length]
            pos += length
            running = None
            if meta_type == 0x51 and length == 3:
                tempo = (payload[0] << 16) | (payload[1] << 8) | payload[2]
                if tempo > 0:
                    events.append((tick, order, "tempo", tempo))
                    order += 1
            elif meta_type == 0x03 and not track_name:
                track_name = payload.decode("utf-8", "replace").strip()
            elif meta_type == 0x04 and not meta_instrument:
                meta_instrument = payload.decode("utf-8", "replace").strip()
            elif meta_type == 0x2F:  # end of track
                break
        elif status in (0xF0, 0xF7):  # sysex
            length, pos = _read_vlq(track, pos)
            pos += length
            running = None
        else:
            running = status
            kind = status & 0xF0
            data_len = 1 if kind in (0xC0, 0xD0) else 2
            payload = track[pos:pos + data_len]
            if len(payload) < data_len:
                break
            pos += data_len
            channel = status & 0x0F
            if kind == 0xC0:  # program change
                events.append((tick, order, "program",
                               (track_index, channel, payload[0])))
                order += 1
            elif channel == DRUM_CHANNEL:
                continue
            elif kind == 0x90 and payload[1] > 0:
                events.append((tick, order, "on",
                               (track_index, channel, payload[0])))
                order += 1
            elif kind == 0x80 or (kind == 0x90 and payload[1] == 0):
                events.append((tick, order, "off",
                               (track_index, channel, payload[0])))
                order += 1
    return events, order, tick, track_name, meta_instrument


def _tempo_converter(tempos, ticks_per_beat):
    """Build tick -> seconds from sorted (tick, us_per_beat) events."""
    segs = [(0, 0.0, DEFAULT_TEMPO)]
    for tick, tempo in sorted(tempos):
        prev_tick, prev_sec, prev_tempo = segs[-1]
        if tick == prev_tick:
            segs[-1] = (tick, prev_sec, tempo)  # last tempo at a tick wins
            continue
        sec = prev_sec + (tick - prev_tick) * prev_tempo / 1e6 / ticks_per_beat
        segs.append((tick, sec, tempo))
    starts = [s[0] for s in segs]

    def to_seconds(tick):
        i = max(bisect_right(starts, tick) - 1, 0)
        start_tick, start_sec, tempo = segs[i]
        return start_sec + (tick - start_tick) * tempo / 1e6 / ticks_per_beat

    return to_seconds, segs[0][2]


def _top_voice_onsets(intervals):
    """Highest note beginning at each onset, treating every note as a tap.

    The instrument cannot hold notes, so only simultaneity matters: a held
    high note does not hide lower notes that start later. Returns
    (start_seconds, end_seconds, pitch) sorted by start, one note per onset;
    end is kept as a length hint only.
    """
    best = {}
    for start, end, pitch in intervals:
        if end < start:
            end = start
        current = best.get(start)
        if current is None or pitch > current[1]:
            best[start] = (end, pitch)
    notes = [(start, end, pitch)
             for start, (end, pitch) in sorted(best.items())]
    if notes:
        # The last chosen pitch may be shorter than another note at that
        # onset; the source's tail still sets the song/loop length.
        source_end = max(end for _start, end, _pitch in intervals)
        if source_end > notes[-1][1]:
            notes[-1] = (notes[-1][0], source_end, notes[-1][2])
    return notes


def _top_voice(intervals):
    """Reduce overlapping (start, end, pitch) notes to one line: the top note."""
    if not intervals:
        return []
    starts, ends = {}, {}
    for start, end, pitch in intervals:
        if end <= start:
            continue
        starts.setdefault(start, []).append(pitch)
        ends.setdefault(end, []).append(pitch)
    points = sorted(set(starts) | set(ends))

    counts = [0] * 128
    segments = []
    for point, next_point in zip(points, points[1:]):
        for pitch in ends.get(point, ()):
            counts[pitch] -= 1
        for pitch in starts.get(point, ()):
            counts[pitch] += 1
        if next_point <= point:
            continue
        top = next((p for p in range(127, -1, -1) if counts[p] > 0), None)
        if top is None:
            continue
        if segments and segments[-1][2] == top and segments[-1][1] == point:
            segments[-1] = (segments[-1][0], next_point, top)
        else:
            segments.append((point, next_point, top))
    return segments


def _to_notes(intervals, tempos, ticks_per_beat, scale=1.0):
    """Turn note intervals into (midi|None, seconds) starting at the first sound."""
    segments = _top_voice(intervals)
    if not segments:
        raise ValueError("MIDI file contains no playable notes")
    to_seconds, _initial = _tempo_converter(tempos, ticks_per_beat)

    notes = []
    cursor = segments[0][0]
    for start, end, pitch in segments:
        if start > cursor:
            notes.append((None, (to_seconds(start) - to_seconds(cursor))
                          * scale))
        length = (to_seconds(end) - to_seconds(start)) * scale
        if length > 1e-6:
            notes.append((pitch, length))
        cursor = end
    return notes


def _default_part(parts):
    """Best guess at the melody: a name hint, else the highest median pitch."""
    hints = ("melody", "lead", "vocal", "solo", "voice", "tune", "right")

    def hinted(part):
        text = f"{part.track_name} {part.instrument or ''}".casefold()
        return any(hint in text for hint in hints)

    pool = [p for p in parts if hinted(p)] or parts
    return max(pool, key=lambda p: (p.median_pitch, p.note_count)).index


def read(path):
    """Parse a .mid/.midi file into a MidiFile (parts kept separate)."""
    with open(path, "rb") as f:
        data = f.read()
    tracks, ticks_per_beat = _read_header(data)

    events, order = [], 0
    track_names, meta_instruments = {}, {}
    for track_index, track in enumerate(tracks):
        track_events, order, _last_tick, name, meta_inst = _read_track(
            track, order, track_index)
        events.extend(track_events)
        track_names[track_index] = name
        meta_instruments[track_index] = meta_inst
    events.sort(key=lambda e: (e[0], e[1]))
    if not events:
        raise ValueError("MIDI file contains no playable notes")

    tempos = [(tick, payload) for tick, _order, kind, payload in events
              if kind == "tempo"]
    _to_seconds, initial_tempo = _tempo_converter(tempos, ticks_per_beat)
    programs = {}
    active = {}
    groups = {}
    last_tick = 0

    for tick, _order, kind, payload in events:
        last_tick = max(last_tick, tick)
        if kind == "program":
            track, channel, program = payload
            programs.setdefault((track, channel), program)
        elif kind == "on":
            active.setdefault(payload, []).append(tick)
        elif kind == "off":
            starts = active.get(payload)
            if starts:
                track, channel, pitch = payload
                groups.setdefault((track, channel), []).append(
                    (starts.pop(0), tick, pitch))
    for (track, channel, pitch), starts in active.items():
        for start in starts:
            groups.setdefault((track, channel), []).append(
                (start, last_tick, pitch))

    channels_per_track = {}
    for track, channel in groups:
        channels_per_track.setdefault(track, set()).add(channel)

    parts = []
    for (track, channel) in sorted(groups):
        intervals = groups[(track, channel)]
        pitches = sorted(pitch for _start, _end, pitch in intervals)
        if not pitches:
            continue
        program = programs.get((track, channel))
        instrument = (GM_INSTRUMENTS[program]
                      if program is not None and 0 <= program < 128 else None)
        track_name = track_names.get(track, "")
        if not track_name and len(channels_per_track[track]) == 1:
            track_name = meta_instruments.get(track, "")
        parts.append(Part(
            index=len(parts),
            track=track,
            channel=channel,
            track_name=track_name,
            program=program,
            instrument=instrument,
            note_count=len(intervals),
            low_pitch=pitches[0],
            high_pitch=pitches[-1],
            median_pitch=pitches[len(pitches) // 2],
            intervals=intervals,
        ))

    if not parts:
        raise ValueError("MIDI file contains no playable notes")

    return MidiFile(
        path=path,
        ticks_per_beat=ticks_per_beat,
        tempo_events=tempos,
        bpm=60_000_000 / initial_tempo,
        parts=parts,
        default_part=_default_part(parts),
        base_bpm=60_000_000 / initial_tempo,
        time_scale=1.0,
    )
