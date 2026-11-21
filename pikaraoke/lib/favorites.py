"""Private favorites lists from the BigCity Nights karaoke website (fork addition).

The website lets each member collect favorites before a party. The karaoke
project's NUC sync (nuc-sync/karaoke_sync.py) fetches all lists while the NUC has
internet and writes them to one JSON file, keyed by the sha256 of each member's
8-digit party code. At the party, without internet, a phone unlocks its own list
by typing that code once. Nothing here talks to the network.
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from pathlib import Path

DEFAULT_FILE = Path.home() / ".local" / "share" / "karaoke-sync" / "favorites.json"

_CODE = re.compile(r"^\d{8}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


def favorites_file() -> Path:
    """Where the NUC sync writes the lists; an environment variable can move it."""
    return Path(os.environ.get("PIKARAOKE_FAVORITES_FILE") or DEFAULT_FILE)


def code_hash(code: str) -> str | None:
    """The key a party code is stored under, or None if it is not 8 digits.

    Spaces and dashes are allowed because the website shows the code as "4827 9135".
    """
    digits = re.sub(r"[\s-]", "", code)
    if not _CODE.match(digits):
        return None
    return hashlib.sha256(digits.encode("ascii")).hexdigest()


def _valid_song(song: object) -> dict | None:
    if not isinstance(song, dict):
        return None
    title, artist, file = song.get("title"), song.get("artist"), song.get("file")
    if not isinstance(title, str) or not isinstance(artist, str):
        return None
    # The sync only writes a bare file name. Anything with a path in it would let
    # the file point outside the songs folder.
    if file is not None and (not isinstance(file, str) or not file or "/" in file or "\\" in file):
        return None
    return {"title": title, "artist": artist, "file": file}


def parse_lists(data: object) -> dict[str, dict]:
    """The lists by code hash. Malformed lists are skipped, not fatal."""
    lists: dict[str, dict] = {}
    if not isinstance(data, dict) or not isinstance(data.get("lists"), list):
        return lists
    for entry in data["lists"]:
        if not isinstance(entry, dict):
            continue
        code, name, songs = entry.get("code"), entry.get("name"), entry.get("songs")
        if not isinstance(code, str) or not _HASH.match(code):
            continue
        if not isinstance(name, str) or not 1 <= len(name) <= 100:
            continue
        if not isinstance(songs, list):
            continue
        valid = [s for s in (_valid_song(song) for song in songs) if s is not None]
        lists[code] = {"name": name, "songs": valid}
    return lists


class FavoritesStore:
    """Reads the favorites file again whenever the sync has replaced it."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._mtime: int | None = None
        self._lists: dict[str, dict] = {}
        self._lock = threading.Lock()

    def lists(self) -> dict[str, dict]:
        try:
            mtime = self._path.stat().st_mtime_ns
        except OSError:
            return {}
        with self._lock:
            if mtime != self._mtime:
                try:
                    self._lists = parse_lists(json.loads(self._path.read_text("utf-8")))
                except (OSError, ValueError) as error:
                    logging.error("Could not read the favorites file %s: %s", self._path, error)
                    self._lists = {}
                self._mtime = mtime
            return self._lists

    def get(self, key: str | None) -> dict | None:
        if not key:
            return None
        return self.lists().get(key)


class WrongCodeLimit:
    """Slows down guessing: an 8-digit code is only safe while tries are few.

    Counts wrong tries per client and for all clients together, so neither one
    phone nor many can try more than a handful of codes per window.
    """

    def __init__(
        self,
        per_client: int = 5,
        overall: int = 30,
        window: float = 600,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._per_client = per_client
        self._overall = overall
        self._window = window
        self._clock = clock
        self._clients: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()
        self._lock = threading.Lock()

    def _expire(self, times: deque[float], now: float) -> None:
        while times and times[0] <= now - self._window:
            times.popleft()

    def blocked(self, client: str) -> bool:
        now = self._clock()
        with self._lock:
            self._expire(self._all, now)
            for key in list(self._clients):
                self._expire(self._clients[key], now)
                if not self._clients[key]:
                    del self._clients[key]
            mine = self._clients.get(client, ())
            return len(mine) >= self._per_client or len(self._all) >= self._overall

    def failed(self, client: str) -> None:
        now = self._clock()
        with self._lock:
            self._clients.setdefault(client, deque()).append(now)
            self._all.append(now)


def songs_with_paths(songs: list[dict], library: Iterable[str]) -> list[dict]:
    """Each favorite with the full path of its video on this machine, or None."""
    by_name = {os.path.basename(path): path for path in library}
    return [{**song, "path": by_name.get(song["file"]) if song["file"] else None} for song in songs]
