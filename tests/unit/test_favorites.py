"""Tests for the favorites tab (fork addition)."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.lib.favorites import (
    FavoritesStore,
    WrongCodeLimit,
    code_hash,
    parse_lists,
    songs_with_paths,
)
from pikaraoke.routes.favorites import favorites_bp
from tests.conftest import make_route_app

# sha256("12345678"); the website's tests pin the same value.
HASH = "ef797c8118f02dfb649607dd5d3f8c7623048c9c063d532cc95c5ed7a898a64f"
FILE = "Song---abcdefghijk.mp4"


def _write(path, lists):
    path.write_text(json.dumps({"generatedAt": "2026-11-21T19:37:00Z", "lists": lists}))


def _list(**changes):
    entry = {
        "code": HASH,
        "name": "Fabi",
        "songs": [
            {"title": "Song", "artist": "Band", "file": FILE},
            {"title": "Missing", "artist": "Band", "file": None},
        ],
    }
    entry.update(changes)
    return entry


def test_code_hash_accepts_the_shown_format():
    assert code_hash("12345678") == HASH
    assert code_hash(" 1234 5678 ") == HASH
    assert code_hash("1234-5678") == HASH


@pytest.mark.parametrize("code", ["", "1234567", "123456789", "abcdefgh", "1234 567x"])
def test_code_hash_refuses_other_input(code):
    assert code_hash(code) is None


def test_parse_lists_skips_malformed_entries():
    lists = parse_lists(
        {
            "lists": [
                _list(),
                _list(code="not-a-hash"),
                _list(code="a" * 64, name=""),
                _list(code="b" * 64, songs="nope"),
                "junk",
            ]
        }
    )
    assert list(lists) == [HASH]
    assert lists[HASH]["name"] == "Fabi"


def test_parse_lists_drops_songs_with_a_path():
    songs = [
        {"title": "A", "artist": "B", "file": "../etc/passwd"},
        {"title": "A", "artist": "B", "file": "x\\y.mp4"},
        {"title": "A", "artist": "B", "file": FILE},
    ]
    lists = parse_lists({"lists": [_list(songs=songs)]})
    assert [s["file"] for s in lists[HASH]["songs"]] == [FILE]


@pytest.mark.parametrize("data", [None, [], {"lists": "x"}, {"other": []}])
def test_parse_lists_of_garbage_is_empty(data):
    assert parse_lists(data) == {}


def test_store_rereads_a_replaced_file(tmp_path):
    path = tmp_path / "favorites.json"
    store = FavoritesStore(path)
    assert store.get(HASH) is None  # no file yet
    _write(path, [_list()])
    assert store.get(HASH)["name"] == "Fabi"
    _write(path, [_list(name="Fabian")])
    os.utime(path, ns=(1, 1))  # a new mtime even on a coarse clock
    assert store.get(HASH)["name"] == "Fabian"


def test_store_survives_a_broken_file(tmp_path):
    path = tmp_path / "favorites.json"
    path.write_text("{broken")
    assert FavoritesStore(path).get(HASH) is None


def test_limit_per_client_and_overall():
    now = [0.0]
    limit = WrongCodeLimit(per_client=2, overall=3, window=60, clock=lambda: now[0])
    limit.failed("a")
    assert not limit.blocked("a")
    limit.failed("a")
    assert limit.blocked("a")
    assert not limit.blocked("b")
    limit.failed("b")
    assert limit.blocked("c")  # three wrong tries in total stop everyone
    now[0] = 61
    assert not limit.blocked("a")
    assert not limit.blocked("c")


def test_songs_with_paths_matches_by_file_name():
    songs = parse_lists({"lists": [_list()]})[HASH]["songs"]
    result = songs_with_paths(songs, ["/songs/" + FILE, "/songs/Other---bbbbbbbbbbb.mp4"])
    assert [s["path"] for s in result] == ["/songs/" + FILE, None]


@pytest.fixture
def favorites_path(tmp_path):
    path = tmp_path / "favorites.json"
    _write(path, [_list()])
    return path


@pytest.fixture
def karaoke():
    k = MagicMock()
    k.song_manager.songs = ["/songs/" + FILE]
    k.queue_manager.enqueue.return_value = [True, "Song added to the queue: Song"]
    return k


@pytest.fixture
def app(favorites_path, karaoke):
    app = make_route_app(favorites_bp, [])
    app.secret_key = "test"
    app.config["FAVORITES_STORE"] = FavoritesStore(favorites_path)
    app.config["FAVORITES_LIMIT"] = WrongCodeLimit(per_client=3, overall=10)
    with (
        patch("pikaraoke.routes.favorites.get_karaoke_instance", return_value=karaoke),
        patch("pikaraoke.routes.favorites.get_site_name", return_value="PiKaraoke"),
        patch("pikaraoke.routes.favorites.is_admin", return_value=False),
        patch("pikaraoke.routes.favorites.broadcast_event"),
    ):
        yield app


def test_page_asks_for_the_code_first(client):
    body = client.get("/favorites").get_data(as_text=True)
    assert 'name="code"' in body
    assert "Song" not in body


def test_right_code_opens_the_list(client):
    response = client.post("/favorites/unlock", data={"code": "1234 5678"})
    assert response.status_code == 302
    body = client.get("/favorites").get_data(as_text=True)
    assert "Fabi" in body
    assert f'data-file="{FILE}"' in body
    assert "No video" in body  # the second favorite has no file


def test_wrong_codes_are_limited(client):
    for _ in range(3):
        client.post("/favorites/unlock", data={"code": "00000000"})
    client.post("/favorites/unlock", data={"code": "12345678"})
    body = client.get("/favorites").get_data(as_text=True)
    assert 'name="code"' in body  # blocked, although the code was right


def test_forged_cookie_opens_nothing(client):
    client.set_cookie("favorites", HASH)
    assert 'name="code"' in client.get("/favorites").get_data(as_text=True)


def test_lock_forgets_the_list(client):
    client.post("/favorites/unlock", data={"code": "12345678"})
    client.post("/favorites/lock")
    assert 'name="code"' in client.get("/favorites").get_data(as_text=True)


def test_enqueue_uses_the_list_name(client, karaoke):
    client.post("/favorites/unlock", data={"code": "12345678"})
    response = client.post("/favorites/enqueue", data={"file": FILE})
    assert json.loads(response.get_data(as_text=True))["success"][0] is True
    karaoke.queue_manager.enqueue.assert_called_once_with("/songs/" + FILE, "Fabi")


def test_enqueue_needs_an_open_list(client, karaoke):
    response = client.post("/favorites/enqueue", data={"file": FILE})
    assert response.status_code == 403
    karaoke.queue_manager.enqueue.assert_not_called()


def test_enqueue_only_songs_of_the_list(client, karaoke):
    client.post("/favorites/unlock", data={"code": "12345678"})
    response = client.post("/favorites/enqueue", data={"file": "Other---bbbbbbbbbbb.mp4"})
    assert response.status_code == 403
    karaoke.queue_manager.enqueue.assert_not_called()
