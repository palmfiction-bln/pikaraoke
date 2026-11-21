# BigCity Nights fork

This fork of [PiKaraoke](https://github.com/vicwomg/pikaraoke) (GPL-3.0, like
the original) adds one thing for a private karaoke circle: a **Favorites**
tab. Everything else is upstream PiKaraoke.

- Branch `bcn` holds the fork: an upstream release tag plus the additions.
  `master` mirrors upstream and is not used.
- How the lists get here: the companion project's NUC sync fetches each
  member's private favorites from the karaoke website while the machine has
  internet and writes them to `~/.local/share/karaoke-sync/favorites.json`
  (override with `PIKARAOKE_FAVORITES_FILE`). Each list is stored under the
  sha256 of the member's 8-digit party code.
- At the party (no internet needed) a phone opens Favorites, types the code
  once and can queue its songs; the queue shows the member's name. Wrong
  codes are limited (5 per phone and 30 overall per 10 minutes), because an
  8-digit code is only safe while guessing is slow.

## Files of the fork

- `pikaraoke/lib/favorites.py`: reading the file, codes, the wrong-code limit
- `pikaraoke/routes/favorites.py`: the routes under `/favorites`
- `pikaraoke/templates/favorites.html`: the page
- `tests/unit/test_favorites.py`
- small hooks: the nav link in `templates/base.html`, the blueprint in
  `app.py`, the endpoint in `tests/conftest.py`, German texts at the end of
  `translations/de_DE/LC_MESSAGES/messages.po`

## Taking a new upstream release

```bash
git fetch upstream --tags
git switch bcn && git switch -c update-<version>
git merge <version>     # conflicts, if any, are in the small hooks above
```

Then open a pull request into `bcn`; CI must be green.
