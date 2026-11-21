"""Per-song settings persisted in SongSettings.json next to the executable.

The file records the user's choices for each MIDI file, keyed by file name:

    {
      "songs": {
        "arrangement.mid": {
          "bpm": 140.0,             # tempo override, omitted when unchanged
          "parts": [2, 0],          # selected instruments, priority order
          "key": "G#",              # in-game key setting
          "key_shift": -1,          # the same key relative to A
          "passes": [               # split mode: one key per playthrough
            {"key": "A", "key_shift": 0},
            {"key": "C", "key_shift": -9}
          ]
        }
      }
    }

The MIDI file itself is never modified. Writes are atomic (temp file +
os.replace), and a missing or corrupt file simply starts empty. The file
lives beside Lute.exe when frozen, otherwise beside this module.
"""
import json
import os
import sys
import tempfile

FILENAME = "SongSettings.json"


def app_dir():
    """Folder the app's user data belongs in (exe folder when frozen)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def settings_path():
    return os.path.join(app_dir(), FILENAME)


class SongSettings:
    """Load, query and atomically save the per-song settings map."""

    def __init__(self, path=None):
        self.path = path or settings_path()
        self.data = self._read()

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            return {}
        songs = payload.get("songs") if isinstance(payload, dict) else None
        if not isinstance(songs, dict):
            return {}
        return {name: dict(entry) for name, entry in songs.items()
                if isinstance(name, str) and isinstance(entry, dict)}

    def get(self, name):
        """A copy of one song's saved settings ({} when none)."""
        return dict(self.data.get(name, {}))

    def update(self, name, **values):
        """Merge values for one song and save. A None value removes the key."""
        entry = dict(self.data.get(name, {}))
        for key, value in values.items():
            if value is None:
                entry.pop(key, None)
            else:
                entry[key] = value
        if entry:
            self.data[name] = entry
        else:
            self.data.pop(name, None)
        return self.save()

    def save(self):
        payload = {"songs": {name: self.data[name]
                             for name in sorted(self.data)}}
        try:
            folder = os.path.dirname(self.path) or "."
            os.makedirs(folder, exist_ok=True)
            fd, temp = tempfile.mkstemp(prefix=".SongSettings-",
                                        suffix=".tmp", dir=folder)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=2, ensure_ascii=False)
                    fh.write("\n")
                os.replace(temp, self.path)
            except Exception:
                try:
                    os.unlink(temp)
                except OSError:
                    pass
                raise
        except OSError:
            # Settings are a convenience; a read-only folder must not break
            # playback.
            return False
        return True
