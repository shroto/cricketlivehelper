#!/usr/bin/env python3
"""Builds players.json (all-time career stats) from the free Cricsheet match data.

Run it any time:  python stats.py
It downloads every Test, ODI, T20I and IPL match, adds up each player's career,
and writes players.json. It uses only Python's standard library.
"""
import io
import json
import os
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone

SOURCES = {
    "Test": "https://cricsheet.org/downloads/tests_json.zip",
    "ODI": "https://cricsheet.org/downloads/odis_json.zip",
    "T20I": "https://cricsheet.org/downloads/t20s_json.zip",
    "IPL": "https://cricsheet.org/downloads/ipl_json.zip",
}
OUT_FILE = "players.json"
MATCHES = []    # one short record for every match, saved by year in the matches/ folder
MATCH_DIR = "matches"
COVERAGE = {}   # per format: how many matches were read, and the first and last match dates
MANUAL_FILE = "manual_stats.json"   # career totals you type in by hand
NAMES_FILE = "names.json"   # your own corrections, e.g. "V Kohli": "Virat Kohli"

# Dismissals that count as a wicket for the bowler
BOWLER_WICKETS = {"caught", "bowled", "lbw", "stumped", "caught and bowled", "hit wicket"}
# Not a dismissal for the batter
NOT_OUT_KINDS = {"retired hurt", "retired not out"}


def new_bat():
    return {"inns": 0, "runs": 0, "balls": 0, "outs": 0, "hs": 0, "hs_no": False,
            "fours": 0, "sixes": 0, "fifties": 0, "hundreds": 0, "ducks": 0}


def new_bowl():
    return {"inns": 0, "balls": 0, "runs": 0, "wkts": 0, "mdns": 0,
            "best_w": 0, "best_r": 0, "fivers": 0}


def get_player(players, pid, name):
    p = players.get(pid)
    if p is None:
        p = {"id": pid, "n": name, "team": "", "last": "", "mat": {}, "bat": {}, "bowl": {}}
        players[pid] = p
    return p


def result_text(info):
    """A short result line, like "India won by 5 wickets" or "Match drawn"."""
    o = info.get("outcome") or {}
    winner = o.get("winner")
    by = o.get("by") or {}
    method = o.get("method")
    if winner:
        if "innings" in by:
            txt = f"{winner} won by an innings and {by.get('runs', 0)} runs"
        elif "runs" in by:
            txt = f"{winner} won by {by['runs']} run{'s' if by['runs'] != 1 else ''}"
        elif "wickets" in by:
            txt = f"{winner} won by {by['wickets']} wicket{'s' if by['wickets'] != 1 else ''}"
        else:
            txt = f"{winner} won"
        return txt + (f" ({method})" if method else "")
    if o.get("eliminator"):
        return f"Match tied, {o['eliminator']} won the super over"
    res = o.get("result")
    if res == "tie":
        return "Match tied"
    if res == "draw":
        return "Match drawn"
    if res == "no result":
        return "No result"
    return res or ""


def process_match(match, fmt, players):
    info = match.get("info", {})
    people = info.get("registry", {}).get("people", {})
    date = (info.get("dates") or [""])[0]
    c = COVERAGE.setdefault(fmt, {"matches": 0, "first": "", "last": ""})
    c["matches"] += 1
    if date:
        if not c["first"] or date < c["first"]:
            c["first"] = date
        if date > c["last"]:
            c["last"] = date

    # one short record for the match archive
    ev = info.get("event") or {}
    toss = info.get("toss") or {}
    rec = {"d": date, "f": fmt, "t": info.get("teams", []), "v": info.get("venue", ""), "c": info.get("city", ""),
           "r": result_text(info), "i": []}
    win = (info.get("outcome") or {}).get("winner") or (info.get("outcome") or {}).get("eliminator")
    if win:
        rec["w"] = win
    if info.get("player_of_match"):
        rec["p"] = info["player_of_match"][:2]
    if ev.get("name"):
        rec["e"] = ev["name"] + (f", match {ev['match_number']}" if ev.get("match_number") else "")
    if toss.get("winner"):
        rec["o"] = f"{toss['winner']} won the toss and chose to {toss.get('decision', 'bat')}"
    MATCHES.append(rec)

    # Matches played
    for team, names in info.get("players", {}).items():
        for name in names:
            pid = people.get(name)
            if not pid:
                continue
            p = get_player(players, pid, name)
            p["mat"][fmt] = p["mat"].get(fmt, 0) + 1
            # latest national team (IPL only fills the team if nothing else is known)
            if fmt != "IPL":
                if date >= p["last"]:
                    p["last"], p["team"] = date, team
            elif not p["team"]:
                p["team"] = team

    for inn in match.get("innings", []):
        if inn.get("super_over"):
            continue
        bat, bowl, overs = {}, {}, {}
        tot_runs = tot_wkts = tot_balls = 0   # the innings total, for the match archive
        for over in inn.get("overs", []):
            for d in over.get("deliveries", []):
                d_ext = d.get("extras", {})
                tot_runs += d.get("runs", {}).get("total", 0)
                if "wides" not in d_ext and "noballs" not in d_ext:
                    tot_balls += 1
                tot_wkts += sum(1 for k in d.get("wickets", []) if k.get("kind") not in NOT_OUT_KINDS)
                batter = people.get(d.get("batter"))
                bowler = people.get(d.get("bowler"))
                if not batter or not bowler:
                    continue
                runs = d.get("runs", {})
                extras = d.get("extras", {})
                wide = "wides" in extras
                noball = "noballs" in extras
                legal = not wide and not noball
                br = runs.get("batter", 0)

                b = bat.setdefault(batter, {"runs": 0, "balls": 0, "fours": 0, "sixes": 0, "out": False})
                b["runs"] += br
                if not wide:
                    b["balls"] += 1
                if not runs.get("non_boundary"):
                    if br == 4:
                        b["fours"] += 1
                    elif br == 6:
                        b["sixes"] += 1

                w = bowl.setdefault(bowler, {"balls": 0, "runs": 0, "wkts": 0})
                charged = br + extras.get("wides", 0) + extras.get("noballs", 0)
                w["runs"] += charged
                if legal:
                    w["balls"] += 1
                om = overs.setdefault((bowler, over.get("over")), [0, 0])
                om[0] += charged
                if legal:
                    om[1] += 1

                for wk in d.get("wickets", []):
                    out_id = people.get(wk.get("player_out"))
                    kind = wk.get("kind", "")
                    if out_id:
                        o = bat.setdefault(out_id, {"runs": 0, "balls": 0, "fours": 0, "sixes": 0, "out": False})
                        if kind not in NOT_OUT_KINDS:
                            o["out"] = True
                    if kind in BOWLER_WICKETS:
                        w["wkts"] += 1

        rec["i"].append([inn.get("team", ""), tot_runs, tot_wkts, tot_balls, 1 if inn.get("declared") else 0])
        names = {v: k for k, v in people.items()}
        for pid, b in bat.items():
            p = get_player(players, pid, names.get(pid, ""))
            s = p["bat"].setdefault(fmt, new_bat())
            s["inns"] += 1
            s["runs"] += b["runs"]
            s["balls"] += b["balls"]
            s["fours"] += b["fours"]
            s["sixes"] += b["sixes"]
            if b["out"]:
                s["outs"] += 1
                if b["runs"] == 0:
                    s["ducks"] += 1
            if b["runs"] > s["hs"]:
                s["hs"], s["hs_no"] = b["runs"], not b["out"]
            if b["runs"] >= 100:
                s["hundreds"] += 1
            elif b["runs"] >= 50:
                s["fifties"] += 1

        for pid, w in bowl.items():
            if w["balls"] == 0 and w["runs"] == 0:
                continue
            p = get_player(players, pid, names.get(pid, ""))
            s = p["bowl"].setdefault(fmt, new_bowl())
            s["inns"] += 1
            s["balls"] += w["balls"]
            s["runs"] += w["runs"]
            s["wkts"] += w["wkts"]
            s["mdns"] += sum(1 for (bid, _), (r, n) in overs.items() if bid == pid and n >= 6 and r == 0)
            if w["wkts"] > s["best_w"] or (w["wkts"] == s["best_w"] and w["wkts"] > 0 and w["runs"] < s["best_r"]):
                s["best_w"], s["best_r"] = w["wkts"], w["runs"]
            if w["wkts"] >= 5:
                s["fivers"] += 1


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": "cricket-live-stats/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def build():
    players = {}
    for fmt, url in SOURCES.items():
        print(f"Downloading {fmt} ...", flush=True)
        data = download(url)
        count = 0
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in z.namelist():
                if not name.endswith(".json"):
                    continue
                process_match(json.loads(z.read(name)), fmt, players)
                count += 1
        print(f"  {fmt}: {count} matches", flush=True)
    return players


def load_names():
    """Reads names.json: {"V Kohli": "Virat Kohli", ...}. The key can be the short
    name from Cricsheet or the player's Cricsheet id. A missing file is fine."""
    try:
        with open(NAMES_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, str) and v.strip()}


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0


def load_manual():
    try:
        with open(MANUAL_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)}


def apply_manual(players):
    """Career totals typed by hand replace the computed ones for that player and format."""
    manual = load_manual()
    if not manual:
        return
    by_name = {}
    for p in players.values():
        by_name.setdefault(p["n"], p)
    for key, e in manual.items():
        p = players.get(key) or by_name.get(key)
        if p is None:
            pid = "manual-" + "".join(c for c in key.lower() if c.isalnum())
            p = get_player(players, pid, key)
        if e.get("name"):
            p["n"] = str(e["name"])
        if e.get("team"):
            p["team"], p["last"] = str(e["team"]), "9999"
        for fmt in SOURCES:
            f = e.get(fmt)
            if not isinstance(f, dict):
                continue
            p["mat"][fmt] = _int(f.get("matches"))
            p["bat"].pop(fmt, None)
            p["bowl"].pop(fmt, None)
            b = f.get("bat")
            if isinstance(b, dict):
                s = new_bat()
                for k in ("inns", "runs", "balls", "outs", "fours", "sixes", "fifties", "hundreds", "ducks"):
                    s[k] = _int(b.get(k))
                hs = str(b.get("hs", "0"))
                s["hs_no"], s["hs"] = hs.endswith("*"), _int(hs.rstrip("*"))
                p["bat"][fmt] = s
            w = f.get("bowl")
            if isinstance(w, dict):
                s = new_bowl()
                for k in ("inns", "runs", "wkts", "mdns", "fivers"):
                    s[k] = _int(w.get(k))
                if "balls" in w:
                    s["balls"] = _int(w["balls"])
                elif "overs" in w:
                    whole, _, part = str(w["overs"]).partition(".")
                    s["balls"] = _int(whole) * 6 + _int(part)
                best = str(w.get("best", "0/0")).split("/")
                s["best_w"], s["best_r"] = _int(best[0]), (_int(best[1]) if len(best) > 1 else 0)
                p["bowl"][fmt] = s
        p["manual"] = True


def pack(players):
    apply_manual(players)
    names = load_names()
    out = []
    for p in players.values():
        if not p["mat"]:
            continue
        short = p["n"]
        full = names.get(p["id"]) or names.get(short)
        if full:
            p["n"] = full.strip()
        out.append({
            "id": p["id"], "n": p["n"], "t": p["team"],
            **({"s": short} if full else {}),
            **({"x": 1} if p.get("manual") else {}),
            "m": p["mat"],
            "bat": {f: [s["inns"], s["runs"], s["balls"], s["outs"], s["hs"], int(s["hs_no"]),
                        s["fours"], s["sixes"], s["fifties"], s["hundreds"], s["ducks"]]
                    for f, s in p["bat"].items()},
            "bowl": {f: [s["inns"], s["balls"], s["runs"], s["wkts"], s["mdns"],
                         s["best_w"], s["best_r"], s["fivers"]]
                     for f, s in p["bowl"].items()},
        })
    out.sort(key=lambda x: -sum(x["m"].values()))
    return {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        # column order used by the app
        "bat_cols": ["inns", "runs", "balls", "outs", "hs", "hs_no", "fours", "sixes", "fifties", "hundreds", "ducks"],
        "bowl_cols": ["inns", "balls", "runs", "wkts", "mdns", "best_w", "best_r", "fivers"],
        "coverage": COVERAGE,
        "players": out,
    }


def write_matches(outdir=None):
    """Saves matches/<year>.json for every year, plus matches/index.json (how many matches each year has)."""
    outdir = outdir or MATCH_DIR
    os.makedirs(outdir, exist_ok=True)
    years = {}
    for m in MATCHES:
        years.setdefault((m["d"] or "0000")[:4], []).append(m)
    index = {}
    for y, ms in years.items():
        ms.sort(key=lambda m: m["d"])
        with open(os.path.join(outdir, f"{y}.json"), "w", encoding="utf-8") as f:
            json.dump({"year": y, "matches": ms}, f, separators=(",", ":"), ensure_ascii=False)
        counts = {}
        for m in ms:
            counts[m["f"]] = counts.get(m["f"], 0) + 1
        index[y] = counts
    with open(os.path.join(outdir, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "years": index}, f, separators=(",", ":"))
    return index


if __name__ == "__main__":
    try:
        result = pack(build())
    except Exception as e:  # keep the old players.json if anything fails
        print("Failed:", e, file=sys.stderr)
        sys.exit(1)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, separators=(",", ":"), ensure_ascii=False)
    print(f"Wrote {OUT_FILE} with {len(result['players'])} players")
    years = write_matches()
    print(f"Wrote {len(years)} match files in {MATCH_DIR}/ ({len(MATCHES)} matches)")
