"""Favorites tab: a member's private list from the karaoke website (fork addition)."""

import json

import flask_babel
from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from itsdangerous import BadSignature, URLSafeSerializer

from pikaraoke.lib.current_app import (
    broadcast_event,
    get_karaoke_instance,
    get_site_name,
    is_admin,
)
from pikaraoke.lib.favorites import (
    FavoritesStore,
    WrongCodeLimit,
    code_hash,
    favorites_file,
    songs_with_paths,
)

_ = flask_babel.gettext

favorites_bp = Blueprint("favorites", __name__)

COOKIE = "favorites"
# A party code is typed once and then remembered on the phone for the next parties.
COOKIE_MAX_AGE = 365 * 24 * 3600


def _store() -> FavoritesStore:
    return current_app.config.setdefault("FAVORITES_STORE", FavoritesStore(favorites_file()))


def _limit() -> WrongCodeLimit:
    return current_app.config.setdefault("FAVORITES_LIMIT", WrongCodeLimit())


def _serializer() -> URLSafeSerializer:
    # Signed, so a phone cannot just write some other list's key into the cookie.
    return URLSafeSerializer(current_app.secret_key, salt="favorites")


def _current_key() -> str | None:
    value = request.cookies.get(COOKIE)
    if not value:
        return None
    try:
        key = _serializer().loads(value)
    except BadSignature:
        return None
    return key if isinstance(key, str) else None


def _current_list() -> dict | None:
    return _store().get(_current_key())


@favorites_bp.route("/favorites")
def favorites():
    """The phone's own list, or the code form while none is unlocked."""
    favorites_list = _current_list()
    songs = None
    if favorites_list is not None:
        k = get_karaoke_instance()
        songs = songs_with_paths(favorites_list["songs"], k.song_manager.songs)
    return render_template(
        "favorites.html",
        site_title=get_site_name(),
        # MSG: Title of the page with a member's own favorites list.
        title=_("Favorites"),
        favorites=favorites_list,
        songs=songs,
        admin=is_admin(),
    )


@favorites_bp.route("/favorites/unlock", methods=["POST"])
def unlock():
    """Open a list with its party code; wrong codes are limited."""
    client = request.remote_addr or "unknown"
    limit = _limit()
    if limit.blocked(client):
        # MSG: Shown after too many wrong party codes.
        flash(_("Too many wrong codes. Please wait ten minutes."), "is-danger")
        return redirect(url_for("favorites.favorites"))
    key = code_hash(request.form.get("code", ""))
    if _store().get(key) is None:
        limit.failed(client)
        # MSG: Shown when the party code matches no favorites list.
        flash(_("This code does not open a list. Check it on the website."), "is-danger")
        return redirect(url_for("favorites.favorites"))
    response = redirect(url_for("favorites.favorites"))
    response.set_cookie(
        COOKIE,
        _serializer().dumps(key),
        max_age=COOKIE_MAX_AGE,
        path=current_app.config.get("SESSION_COOKIE_PATH") or "/",
        httponly=True,
        samesite="Lax",
    )
    return response


@favorites_bp.route("/favorites/lock", methods=["POST"])
def lock():
    """Forget the list on this phone, for a phone that is passed around."""
    response = redirect(url_for("favorites.favorites"))
    response.delete_cookie(COOKIE, path=current_app.config.get("SESSION_COOKIE_PATH") or "/")
    return response


@favorites_bp.route("/favorites/enqueue", methods=["POST"])
def enqueue():
    """Queue one song of the unlocked list, under the list's name."""
    favorites_list = _current_list()
    if favorites_list is None:
        return json.dumps({"success": [False, _("Unauthorized")]}), 403
    file = request.form.get("file", "")
    if not any(song["file"] == file for song in favorites_list["songs"]):
        return json.dumps({"success": [False, _("Unauthorized")]}), 403
    k = get_karaoke_instance()
    song = songs_with_paths([{"title": "", "artist": "", "file": file}], k.song_manager.songs)[0]
    if song["path"] is None:
        # MSG: Shown when a favorite's video is not on this machine.
        return json.dumps({"success": [False, _("This video is not on this machine yet.")]})
    rc = k.queue_manager.enqueue(song["path"], favorites_list["name"])
    broadcast_event("queue_update")
    return json.dumps({"success": rc})
