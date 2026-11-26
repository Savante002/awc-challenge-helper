# awc-challenge-helper

UNOFFICIAL, read-only helper for AWC challenges, for AniList. Personal, non-commercial use.

## What it does

`tools/read_list.py` reads my **public** manga list, my favourites and my manga genre stats
through the official AniList API, and builds a short summary used to pick titles for an AWC challenge.

The summary is never stored in the clear: the Action encrypts it with the public key in `keys/`
and commits only `out/bundle.enc`, `out/key.enc` and `out/log.txt` (counts and errors, no titles).
The encrypted files are removed once the picks are made. Nothing here is kept as a backup.

## Rules it follows

- Official API only (`graphql.anilist.co`). No scraping of anilist.co pages.
- Read-only. No token, nothing is written to AniList. No likes, follows, comments or messages.
- One request at a time, 3 seconds apart, at most 30 requests per run.
- On 429 it waits `Retry-After` plus a margin. On 403 it stops.
- Only the fields the challenge needs.

## How to run it

Change `run/request.txt` (any edit) and commit, or use **Run workflow** in the Actions tab.
