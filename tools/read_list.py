#!/usr/bin/env python3
"""UNOFFICIAL, read-only helper for AWC challenges (for AniList).

Reads ONE user's public manga list, favourites and manga genre stats through
the official AniList API, plus the few public facts an AWC challenge asks for
(what a fellow participant rated 90+, what an AWC staff member has as a
favourite). Writes out/data.json, which the Action encrypts before saving.

Rules this script follows:
- official API only (graphql.anilist.co), no token, nothing is written to AniList
- one request at a time, PAUSE seconds between requests, MAX_REQUESTS per run
- on 429: wait Retry-After plus a margin, then go on; on 403: stop the run
- only the fields the challenge needs; the log holds counts, never titles
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
THREAD_ID = int(os.environ.get("AWC_THREAD_ID", "6395"))
PAUSE = 3.0
MAX_REQUESTS = 50

state = {"requests": 0, "last": 0.0}
log_lines = []

MEDIA = ("id format countryOfOrigin chapters volumes status averageScore popularity isAdult genres "
         "title { romaji english } startDate { year }")


class Stop(Exception):
    """The whole run has to stop (403, budget, network)."""


class ApiError(Exception):
    """One query was refused (private user, not found): skip it and go on."""


def log(msg):
    line = "%s %s" % (datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S"), msg)
    log_lines.append(line)
    print(line, flush=True)


def gql(query, variables=None):
    """One POST to the API, paced, with the 429 / 403 rules."""
    for attempt in range(4):
        if state["requests"] >= MAX_REQUESTS:
            raise Stop("request budget of %d used up" % MAX_REQUESTS)
        wait = PAUSE - (time.time() - state["last"])
        if wait > 0:
            time.sleep(wait)
        body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        req = urllib.request.Request(API, data=body, method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json",
            "User-Agent": "awc-challenge-helper (read-only, personal use)"})
        state["requests"] += 1
        state["last"] = time.time()
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            text = err.read().decode("utf-8", "replace")[:300]
            if err.code == 429:
                pause = int(err.headers.get("Retry-After") or 60) + 5
                log("429 received, waiting %d s" % pause)
                time.sleep(pause)
                continue
            if err.code == 403:
                raise Stop("403 from the API, run stopped")
            if err.code >= 500 and attempt == 0:
                log("HTTP %d, one retry in 15 s" % err.code)
                time.sleep(15)
                continue
            if err.code in (400, 404):
                raise ApiError("HTTP %d: %s" % (err.code, text))
            raise Stop("HTTP %d" % err.code)
        except urllib.error.URLError as err:
            raise Stop("network error: %s" % err)
        if data.get("errors"):
            raise ApiError(json.dumps(data["errors"])[:300])
        return data["data"]
    raise Stop("still rate-limited after several waits")


def step(name, func, data):
    """Run one optional part; a refused query skips the part, a Stop ends the run."""
    try:
        func(data)
    except ApiError as err:
        log("%s skipped: %s" % (name, str(err)[:120]))


def read_list(data):
    entries, chunk = {}, 1
    while True:
        col = gql("""query ($user: String, $chunk: Int) {
          MediaListCollection(userName: $user, type: MANGA, chunk: $chunk, perChunk: 500) {
            hasNextChunk lists { entries { status progress progressVolumes score(format: POINT_100)
              media { %s } } } } }""" % MEDIA, {"user": USER, "chunk": chunk})["MediaListCollection"]
        for lst in col.get("lists") or []:
            for e in lst.get("entries") or []:
                entries.setdefault(str(e["media"]["id"]), e)
        if not col.get("hasNextChunk") or chunk >= 6:
            break
        chunk += 1
    data["entries"] = list(entries.values())
    log("%d list entries" % len(entries))


def read_user(data):
    user = gql("""query ($user: String) { User(name: $user) { id name
      statistics { manga { count chaptersRead meanScore genres(sort: COUNT_DESC) { genre count meanScore } } }
      favourites {
        anime(page: 1, perPage: 25) { pageInfo { hasNextPage } nodes { id source(version: 3) title { romaji english } } }
        manga(page: 1, perPage: 25) { nodes { id title { romaji english } } }
        staff(page: 1, perPage: 25) { nodes { id name { full } } } } } }""", {"user": USER})["User"]
    anime = user["favourites"]["anime"]
    nodes, more, page = list(anime.get("nodes") or []), (anime.get("pageInfo") or {}).get("hasNextPage"), 2
    while more and page <= 4:
        part = gql("""query ($user: String, $page: Int) { User(name: $user) { favourites {
          anime(page: $page, perPage: 25) { pageInfo { hasNextPage }
            nodes { id source(version: 3) title { romaji english } } } } } }""",
                   {"user": USER, "page": page})["User"]["favourites"]["anime"]
        nodes.extend(part.get("nodes") or [])
        more, page = (part.get("pageInfo") or {}).get("hasNextPage"), page + 1
    user["favourites"]["anime"] = nodes
    data["user"] = user
    log("%d favourite anime, %d favourite manga" % (len(nodes), len(user["favourites"]["manga"]["nodes"])))


def relations(ids, mtype):
    out, ids, size, i = {}, list(ids), 50, 0
    while i < len(ids):
        part = ids[i:i + size]
        try:
            page = gql("""query ($ids: [Int], $type: MediaType) { Page(page: 1, perPage: 50) {
              media(id_in: $ids, type: $type) { id relations { edges { relationType(version: 2)
                node { type %s } } } } } }""" % MEDIA, {"ids": part, "type": mtype})["Page"]
        except ApiError as err:
            if "complexity" in str(err).lower() and size > 10:
                size = 25 if size == 50 else 10
                continue
            raise
        for m in page.get("media") or []:
            out[str(m["id"])] = [[ed["relationType"], ed["node"]] for ed in (m.get("relations") or {}).get("edges") or []]
        i += len(part)
    return out


def read_relations(data):
    fav = [a["id"] for a in data["user"]["favourites"]["anime"]]
    data["fav_anime_relations"] = relations(fav, "ANIME") if fav else {}
    done = [e["media"]["id"] for e in data["entries"] if e["status"] == "COMPLETED"][:400]
    data["completed_relations"] = relations(done, "MANGA") if done else {}
    log("relations read for %d anime and %d manga" % (len(data["fav_anime_relations"]), len(data["completed_relations"])))


def read_authors(data):
    ids = [m["id"] for m in data["user"]["favourites"]["manga"]["nodes"]]
    data["fav_manga_staff"], data["staff_works"] = {}, {}
    if not ids:
        return
    page = gql("""query ($ids: [Int]) { Page(page: 1, perPage: 25) { media(id_in: $ids, type: MANGA) { id
      staff(page: 1, perPage: 8) { edges { role node { id name { full } } } } } } }""", {"ids": ids})["Page"]
    wanted = []
    for m in page.get("media") or []:
        edges = [[ed["role"], ed["node"]["id"], ed["node"]["name"]["full"]] for ed in (m.get("staff") or {}).get("edges") or []]
        data["fav_manga_staff"][str(m["id"])] = edges
        for role, sid, _ in edges:
            if any(k in (role or "") for k in ("Story", "Art", "Original")) and sid not in wanted:
                wanted.append(sid)
    for sid in wanted[:8]:
        try:
            staff = gql("""query ($id: Int) { Staff(id: $id) { id name { full }
              staffMedia(type: MANGA, page: 1, perPage: 25, sort: POPULARITY_DESC) { edges { staffRole node { %s } } } } }""" % MEDIA,
                        {"id": sid})["Staff"]
        except ApiError:
            continue
        data["staff_works"][str(sid)] = {"name": staff["name"]["full"], "works": [
            [ed["staffRole"], ed["node"]] for ed in (staff.get("staffMedia") or {}).get("edges") or []]}
    log("authors read: %d" % len(data["staff_works"]))


def read_awc_staff(data):
    """Manga favourites of the accounts the AWC profile follows (its staff list)."""
    awc = gql('query { User(name: "AWC") { id } }')["User"]["id"]
    people, page = [], 1
    while page <= 2:
        res = gql("""query ($id: Int!, $page: Int) { Page(page: $page, perPage: 50) { pageInfo { hasNextPage }
          following(userId: $id) { id name } } }""", {"id": awc, "page": page})["Page"]
        people.extend(res.get("following") or [])
        if not (res.get("pageInfo") or {}).get("hasNextPage"):
            break
        page += 1
    out = []
    for i in range(0, len(people), 10):
        part = people[i:i + 10]
        query = "query { %s }" % " ".join(
            "u%d: User(id: %d) { id name favourites { manga(page: 1, perPage: 25) { nodes { id } } } }" % (n, p["id"])
            for n, p in enumerate(part))
        try:
            res = gql(query)
        except ApiError:
            continue
        for u in res.values():
            if u:
                out.append({"name": u["name"], "manga": [m["id"] for m in u["favourites"]["manga"]["nodes"]]})
    data["awc_staff_favourites"] = out
    log("AWC staff favourites read for %d accounts" % len(out))


def read_participants(data):
    """Recent entries in the challenge thread and what those participants rated 90+."""
    res = gql("""query ($thread: Int) { Page(page: 1, perPage: 50) {
      threadComments(threadId: $thread, sort: ID_DESC) { id siteUrl createdAt user { id name } } } }""",
              {"thread": THREAD_ID})["Page"]
    seen, out = set(), []
    for c in res.get("threadComments") or []:
        u = c.get("user") or {}
        if not u.get("id") or u["id"] in seen or (u.get("name") or "").lower() == USER.lower():
            continue
        seen.add(u["id"])
        if len(out) >= 8:
            break
        try:
            top = gql("""query ($uid: Int) { Page(page: 1, perPage: 50) {
              mediaList(userId: $uid, type: MANGA, sort: SCORE_DESC) { score(format: POINT_100) media { id } } } }""",
                      {"uid": u["id"]})["Page"]
        except ApiError:
            continue
        rated = [m["media"]["id"] for m in top.get("mediaList") or [] if (m.get("score") or 0) >= 90]
        out.append({"name": u["name"], "post": c.get("siteUrl"), "rated90": rated})
    data["participants"] = out
    log("participants read: %d" % len(out))


def read_ideas(data):
    """Popular finished titles for the requirements the list itself cannot fill."""
    def page(args):
        return "Page(page: 1, perPage: 25) { media(type: MANGA, isAdult: false, sort: POPULARITY_DESC, %s) { %s } }" % (args, MEDIA)
    res = gql("query { %s }" % " ".join([
        "old: " + page('status: FINISHED, format: MANGA, startDate_lesser: 20000101, chapters_lesser: 90, genre_in: ["Fantasy"]'),
        "oneshot: " + page('format: ONE_SHOT, genre_in: ["Fantasy"]'),
        "short: " + page('status: FINISHED, format: MANGA, chapters_lesser: 11, genre_in: ["Fantasy"]'),
        "horror: " + page('status: FINISHED, chapters_lesser: 80, genre_in: ["Horror"]'),
    ]))
    data["ideas"] = {k: (v or {}).get("media") or [] for k, v in res.items()}
    log("idea lists read: %s" % ", ".join("%s %d" % (k, len(v)) for k, v in data["ideas"].items()))


def main():
    os.makedirs(OUT, exist_ok=True)
    data = {"user_name": USER, "read_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    ok = False
    try:
        if not USER:
            raise Stop("ANILIST_USER is not set")
        read_list(data)
        read_user(data)
        ok = True
        for name, func in (("relations", read_relations), ("authors", read_authors), ("ideas", read_ideas),
                           ("AWC staff", read_awc_staff), ("participants", read_participants)):
            step(name, func, data)
        log("done with %d requests" % state["requests"])
    except (Stop, ApiError) as err:
        log("STOPPED: %s" % str(err)[:200])
    except Exception as err:  # keep the log even when something unexpected breaks
        import traceback
        data["error"] = traceback.format_exc()  # details go into the encrypted file only
        log("ERROR: %s" % type(err).__name__)
    data["requests"] = state["requests"]
    with open(os.path.join(OUT, "data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(OUT, "log.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
