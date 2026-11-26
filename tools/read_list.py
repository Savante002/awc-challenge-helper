#!/usr/bin/env python3
"""UNOFFICIAL, read-only helper for AWC challenges (for AniList).

Reads ONE user's public manga list, favourites and manga genre stats through
the official AniList API and writes a short summary into out/.

Rules this script follows:
- official API only (graphql.anilist.co), no token, nothing is written to AniList
- one request at a time, PAUSE seconds between requests, MAX_REQUESTS per run
- on 429: wait Retry-After plus a margin, then go on; on 403: stop the run
- only the fields the challenge needs
"""
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://graphql.anilist.co"
USER = os.environ.get("ANILIST_USER", "").strip()
OUT = os.environ.get("OUT_DIR", "out")
PAUSE = 3.0
MAX_REQUESTS = 30
REL_OK = {"SEQUEL", "PREQUEL", "SIDE_STORY", "SPIN_OFF", "ALTERNATIVE", "PARENT"}

state = {"requests": 0, "last": 0.0}
log_lines = []


class Stop(Exception):
    pass


def log(msg):
    line = "%s %s" % (datetime.datetime.utcnow().strftime("%H:%M:%S"), msg)
    log_lines.append(line)
    print(line, flush=True)


def gql(query, variables):
    """One POST to the API, paced, with the 429 / 403 rules."""
    for attempt in range(4):
        if state["requests"] >= MAX_REQUESTS:
            raise Stop("request budget of %d used up" % MAX_REQUESTS)
        wait = PAUSE - (time.time() - state["last"])
        if wait > 0:
            time.sleep(wait)
        body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        req = urllib.request.Request(API, data=body, method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json",
            "User-Agent": "awc-challenge-helper (read-only, personal use)"})
        state["requests"] += 1
        state["last"] = time.time()
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            text = err.read().decode("utf-8", "replace")[:400]
            if err.code == 429:
                pause = int(err.headers.get("Retry-After") or 60) + 5
                log("429 received, waiting %d s" % pause)
                time.sleep(pause)
                continue
            if err.code == 403:
                raise Stop("403 from the API, run stopped: " + text)
            if err.code >= 500 and attempt == 0:
                log("HTTP %d, one retry in 15 s" % err.code)
                time.sleep(15)
                continue
            raise Stop("HTTP %d: %s" % (err.code, text))
        except urllib.error.URLError as err:
            raise Stop("network error: %s" % err)
        if data.get("errors"):
            raise Stop("API error: " + json.dumps(data["errors"])[:400])
        return data["data"]
    raise Stop("still rate-limited after several waits")


Q_LIST = """
query ($user: String, $chunk: Int) {
  MediaListCollection(userName: $user, type: MANGA, chunk: $chunk, perChunk: 500) {
    hasNextChunk
    lists { name isCustomList entries {
      status progress progressVolumes repeat score(format: POINT_100)
      media { id format countryOfOrigin chapters volumes status averageScore popularity genres
              title { romaji english } startDate { year } } } }
  }
}"""

Q_USER = """
query ($user: String) {
  User(name: $user) {
    id name
    statistics { manga { count chaptersRead meanScore
      genres(sort: COUNT_DESC) { genre count meanScore chaptersRead } } }
    favourites {
      anime(page: 1, perPage: 25) { pageInfo { hasNextPage }
        nodes { id source(version: 3) title { romaji english } } }
      manga(page: 1, perPage: 25) { nodes { id title { romaji english } } }
      staff(page: 1, perPage: 25) { nodes { id name { full } primaryOccupations } }
    }
  }
}"""

Q_FAV_ANIME = """
query ($user: String, $page: Int) {
  User(name: $user) { favourites { anime(page: $page, perPage: 25) { pageInfo { hasNextPage }
    nodes { id source(version: 3) title { romaji english } } } } }
}"""

Q_REL = """
query ($ids: [Int], $type: MediaType) {
  Page(page: 1, perPage: 50) {
    media(id_in: $ids, type: $type) { id relations { edges { relationType(version: 2)
      node { id type format countryOfOrigin chapters volumes status averageScore popularity
             title { romaji english } startDate { year } } } } }
  }
}"""


def title(media):
    t = media.get("title") or {}
    return t.get("romaji") or t.get("english") or "?"


def both_titles(media):
    t = media.get("title") or {}
    rom, eng = t.get("romaji") or "", t.get("english") or ""
    return rom if not eng or eng == rom else "%s [%s]" % (rom, eng)


def read_list():
    entries, chunk = {}, 1
    while True:
        col = gql(Q_LIST, {"user": USER, "chunk": chunk})["MediaListCollection"]
        for lst in col.get("lists") or []:
            for e in lst.get("entries") or []:
                m = e["media"]
                entries.setdefault(m["id"], {"status": e["status"], "progress": e.get("progress") or 0,
                                             "vols": e.get("progressVolumes") or 0,
                                             "score": e.get("score") or 0, "media": m})
        if not col.get("hasNextChunk") or chunk >= 6:
            return entries
        chunk += 1


def read_relations(ids, mtype):
    out, ids = {}, list(ids)
    size, i = 50, 0
    while i < len(ids):
        part = ids[i:i + size]
        try:
            page = gql(Q_REL, {"ids": part, "type": mtype})["Page"]
        except Stop as err:
            if "complexity" in str(err).lower() and size > 10:
                size = 25 if size == 50 else 10
                log("query too complex, retrying with %d ids per request" % size)
                continue
            raise
        for m in page.get("media") or []:
            out[m["id"]] = [(ed["relationType"], ed["node"]) for ed in (m.get("relations") or {}).get("edges") or []]
        i += len(part)
    return out


def facts(media, entries):
    mine = entries.get(media["id"])
    my = "not on list" if not mine else "%s %s" % (mine["status"], mine["progress"])
    ch = media.get("chapters")
    return "%s | %s/%s | ch %s | %s | %s | avg %s | pop %s | mine: %s | id %s" % (
        both_titles(media), media.get("format"), media.get("countryOfOrigin"),
        ch if ch else "?", media.get("status"), (media.get("startDate") or {}).get("year"),
        media.get("averageScore"), media.get("popularity"), my, media["id"])


def section(lines, name, medias, entries, limit=14):
    medias = sorted(medias, key=lambda m: -(m.get("popularity") or 0))
    lines.append("")
    lines.append("## %s (%d)" % (name, len(medias)))
    for m in medias[:limit]:
        lines.append("- " + facts(m, entries))


def odd_chars(media):
    t = media.get("title") or {}
    found = set()
    for text in (t.get("romaji") or "", t.get("english") or ""):
        found |= {c for c in text if not c.isalnum() and not c.isspace()}
    return found


def summarise(entries, user, fav_rel, done_rel):
    stats = ((user.get("statistics") or {}).get("manga")) or {}
    genres = [g for g in stats.get("genres") or [] if g.get("count")]
    fav = user.get("favourites") or {}
    by_status = {}
    for e in entries.values():
        by_status.setdefault(e["status"], []).append(e)
    lines = ["# Manga list summary for %s" % USER,
             "Read %s UTC with %d API requests." % (
                 datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M"), state["requests"]),
             "Entries: " + ", ".join("%s %d" % (k, len(v)) for k, v in sorted(by_status.items())),
             "Stats: %s manga, %s chapters read, mean score %s" % (
                 stats.get("count"), stats.get("chaptersRead"), stats.get("meanScore")),
             "", "## Genres by count (genre: count, mean score)"]
    lines.append(", ".join("%s: %d, %s" % (g["genre"], g["count"], g["meanScore"]) for g in genres))
    scored = sorted([g for g in genres if g.get("meanScore")], key=lambda g: -g["meanScore"])
    lines.append("")
    lines.append("## Genres by mean score")
    lines.append(", ".join("%s: %s (%d)" % (g["genre"], g["meanScore"], g["count"]) for g in scored))
    top3 = [g["genre"] for g in genres[:3]]

    lines.append("")
    lines.append("## Favourites")
    lines.append("Staff: " + ("; ".join("%s (%s) id %s" % (s["name"]["full"], "/".join(s.get("primaryOccupations") or []), s["id"])
                                         for s in (fav.get("staff") or {}).get("nodes") or []) or "none"))
    lines.append("Manga: " + ("; ".join(title(m) for m in (fav.get("manga") or {}).get("nodes") or []) or "none"))
    lines.append("")
    lines.append("## 01 Favourite anime and their source (%d favourites)" % len(user.get("_anime") or []))
    for a in user.get("_anime") or []:
        lines.append("- %s | source %s" % (both_titles(a), a.get("source")))
        for rtype, node in fav_rel.get(a["id"], []):
            if node.get("type") == "MANGA" and rtype in ("SOURCE", "ADAPTATION", "ALTERNATIVE"):
                lines.append("    %s: %s" % (rtype, facts(node, entries)))

    new = [e["media"] for e in by_status.get("PLANNING", []) if not e["progress"]]
    started = [e for st in ("CURRENT", "PAUSED", "DROPPED") for e in by_status.get(st, [])]
    lines.append("")
    lines.append("## Already started (CURRENT / PAUSED / DROPPED): %d" % len(started))
    for e in sorted(started, key=lambda e: -(e["media"].get("popularity") or 0))[:40]:
        lines.append("- " + facts(e["media"], entries))

    def yr(m):
        return (m.get("startDate") or {}).get("year") or 0

    def fin(m):
        return m.get("status") == "FINISHED"

    section(lines, "04 Planning: has favourite + least favourite genre (see mean-score order)", [
        m for m in new if scored and scored[0]["genre"] in (m.get("genres") or [])
        and scored[-1]["genre"] in (m.get("genres") or [])], entries)
    section(lines, "05 Planning: none of the top 3 genres %s" % top3, [
        m for m in new if top3 and not set(top3) & set(m.get("genres") or [])], entries, 20)
    lines.append("")
    lines.append("## 06 Related to a completed manga, not completed itself")
    seen, rel_lines = set(), []
    for mid, edges in done_rel.items():
        for rtype, node in edges:
            mine = entries.get(node["id"])
            if node.get("type") != "MANGA" or rtype not in REL_OK or node["id"] in seen:
                continue
            if mine and mine["status"] in ("COMPLETED", "REPEATING"):
                continue
            seen.add(node["id"])
            rel_lines.append((-(node.get("popularity") or 0), "- %s  <= %s of completed '%s'" % (
                facts(node, entries), rtype, title(entries[mid]["media"]))))
    lines.extend(text for _, text in sorted(rel_lines)[:30])
    section(lines, "07 Planning list, not started (most popular first)", new, entries, 40)
    section(lines, "08 Planning: non-alphanumeric character in a title", [m for m in new if odd_chars(m)], entries, 12)
    section(lines, "09 Planning: finished, 10 chapters or less, not a one-shot", [
        m for m in new if fin(m) and m.get("chapters") and m["chapters"] <= 10 and m.get("format") != "ONE_SHOT"], entries)
    section(lines, "10 Planning: finished, 100 chapters or more", [
        m for m in new if fin(m) and (m.get("chapters") or 0) >= 100], entries, 20)
    section(lines, "11 Planning: manhwa, manhua or light novel, finished", [
        m for m in new if fin(m) and (m.get("countryOfOrigin") in ("KR", "CN", "TW") or m.get("format") == "NOVEL")], entries, 20)
    section(lines, "12 Planning: one-shots", [m for m in new if m.get("format") == "ONE_SHOT"], entries)
    section(lines, "13 Planning: began in 1999 or earlier", [m for m in new if 0 < yr(m) <= 1999], entries)
    section(lines, "15 Planning: still releasing, not started", [m for m in new if m.get("status") == "RELEASING"], entries, 20)
    section(lines, "16 Planning: finished, average below 70", [
        m for m in new if fin(m) and m.get("averageScore") and m["averageScore"] < 70], entries, 20)
    section(lines, "17 Planning: finished, average 80 or above", [
        m for m in new if fin(m) and (m.get("averageScore") or 0) >= 80], entries, 20)
    section(lines, "Planning: finished, any length (pool for 14 and the rest)", [m for m in new if fin(m)], entries, 60)
    return lines


def main():
    os.makedirs(OUT, exist_ok=True)
    ok = False
    try:
        if not USER:
            raise Stop("ANILIST_USER is not set")
        log("reading the manga list of %s" % USER)
        entries = read_list()
        log("%d entries" % len(entries))
        user = gql(Q_USER, {"user": USER})["User"]
        anime = list(((user.get("favourites") or {}).get("anime") or {}).get("nodes") or [])
        more = (((user.get("favourites") or {}).get("anime") or {}).get("pageInfo") or {}).get("hasNextPage")
        page = 2
        while more and page <= 4:
            part = gql(Q_FAV_ANIME, {"user": USER, "page": page})["User"]["favourites"]["anime"]
            anime.extend(part.get("nodes") or [])
            more, page = (part.get("pageInfo") or {}).get("hasNextPage"), page + 1
        user["_anime"] = anime
        log("%d favourite anime" % len(anime))
        fav_rel = read_relations([a["id"] for a in anime], "ANIME") if anime else {}
        done = sorted((e for e in entries.values() if e["status"] == "COMPLETED"),
                      key=lambda e: -(e["media"].get("popularity") or 0))[:400]
        done_rel = read_relations([e["media"]["id"] for e in done], "MANGA") if done else {}
        log("relations read for %d completed manga" % len(done_rel))
        lines = summarise(entries, user, fav_rel, done_rel)
        with open(os.path.join(OUT, "summary.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        with open(os.path.join(OUT, "completed.txt"), "w", encoding="utf-8") as f:
            for e in sorted(entries.values(), key=lambda e: title(e["media"]).lower()):
                if e["status"] in ("COMPLETED", "REPEATING"):
                    f.write("%s | id %s\n" % (both_titles(e["media"]), e["media"]["id"]))
        ok = True
        log("done with %d requests" % state["requests"])
    except Stop as err:
        log("STOPPED: %s" % err)
    except Exception as err:  # keep the log even when something unexpected breaks
        log("ERROR: %r" % (err,))
    with open(os.path.join(OUT, "log.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
