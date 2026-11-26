# awc-challenge-helper

UNOFFICIAL, read-only helper for AWC challenges, for AniList. Private, personal use.

## What it does

`tools/read_list.py` reads my **public** manga list, my favourites and my manga genre stats
through the official AniList API, and writes a short summary into `out/`.
The summary is used to pick titles for an AWC challenge, then removed.

## Rules it follows

- Official API only (`graphql.anilist.co`). No scraping of anilist.co pages.
- Read-only. No token, nothing is written to AniList. No likes, follows, comments or messages.
- One request at a time, 3 seconds apart, at most 30 requests per run.
- On 429 it waits `Retry-After` plus a margin. On 403 it stops.
- Only the fields the challenge needs. The summary is not kept as a backup.

## How to run it

Change `run/request.txt` (any edit) and commit, or use **Run workflow** in the Actions tab.
The Action commits `out/summary.md`, `out/completed.txt` and `out/log.txt`.
