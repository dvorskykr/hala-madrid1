#!/usr/bin/env python3
"""Aktualizace data.json pro web Hala Madrid.

Zdroj výsledků, programu, tabulek a sestavy: football-data.org (bezplatný klíč FOOTBALL_DATA_KEY).
Zdroj kurzů (volitelně): the-odds-api.com (klíč ODDS_API_KEY, spouští se s parametrem --odds).

Když některý zdroj selže, ponechá se původní část data.json. Skript používá jen standardní knihovnu.
"""
import json
import os
import statistics
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data.json"
TEAM_ID = 86  # Real Madrid ve football-data.org
SEASON = os.environ.get("SEASON", "2026")
FD_KEY = os.environ.get("FOOTBALL_DATA_KEY", "")
ODDS_KEY = os.environ.get("ODDS_API_KEY", "")

CZ = {
    "Inter": "Inter Milán", "Internazionale": "Inter Milán", "FC Internazionale Milano": "Inter Milán",
    "Roma": "AS Řím", "AS Roma": "AS Řím", "Leipzig": "RB Lipsko", "RB Leipzig": "RB Lipsko",
    "Atleti": "Atlético Madrid", "Atlético": "Atlético Madrid", "Club Atlético de Madrid": "Atlético Madrid",
    "AEK": "AEK Athény", "AEK Athens": "AEK Athény", "Barça": "FC Barcelona", "Barcelona": "FC Barcelona",
    "Bayern": "Bayern Mnichov", "FC Bayern München": "Bayern Mnichov", "Shakhtar": "Šachtar Doněck",
    "Slavia Praha": "Slavia Praha", "Napoli": "SSC Neapol", "Celta": "Celta Vigo", "Alavés": "Alavés",
    "Deportivo": "Deportivo La Coruña", "Sociedad": "Real Sociedad", "Betis": "Real Betis",
    "Galatasaray": "Galatasaray", "Fenerbahçe": "Fenerbahce", "Man City": "Manchester City",
    "Man United": "Manchester United", "Dortmund": "Borussia Dortmund", "PSG": "Paris Saint-Germain",
}
COMP = {"PD": "La Liga", "CL": "Liga mistrů", "CDR": "Copa del Rey", "SUC": "Superpohár"}
SURNAME_FIX = {"Júnior": "Vinícius Jr.", "Junior": "Vinícius Jr.", "Silva": "Bernardo Silva", "Díaz": "Brahim Díaz", "Diaz": "Brahim Díaz"}

# Souřadnice na hřišti (x zleva doprava, y shora; nahoře je soupeřova branka)
POS = {
    "Goalkeeper": (50, 91),
    "Right-Back": (86, 72), "Left-Back": (14, 72), "Centre-Back": (50, 76),
    "Right Wing-Back": (88, 66), "Left Wing-Back": (12, 66),
    "Defensive Midfield": (50, 56), "Central Midfield": (50, 50),
    "Right Midfield": (86, 50), "Left Midfield": (14, 50), "Attacking Midfield": (50, 34),
    "Right Winger": (83, 30), "Left Winger": (17, 30),
    "Centre-Forward": (50, 12), "Second Striker": (50, 22),
    # hrubé skupiny, kdyby API poslalo jen je
    "Defence": (50, 75), "Midfield": (50, 50), "Offence": (50, 16),
}


def http_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "hala-madrid-updater"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fd(path):
    return http_json("https://api.football-data.org/v4/" + path, {"X-Auth-Token": FD_KEY})


def cz(team):
    for key in (team.get("shortName"), team.get("name")):
        if key and key in CZ:
            return CZ[key]
    return team.get("shortName") or team.get("name") or "?"


def parse_matches(payload):
    games = []
    for m in payload.get("matches", []):
        home = m["homeTeam"]["id"] == TEAM_ID
        opp = cz(m["awayTeam"] if home else m["homeTeam"])
        code = (m.get("competition") or {}).get("code", "")
        g = {
            "t": m["utcDate"],
            "comp": COMP.get(code, (m.get("competition") or {}).get("name", "")),
            "home": home,
            "opp": opp,
            "id": m.get("id"),
        }
        if m.get("status") == "FINISHED":
            ft = (m.get("score") or {}).get("fullTime") or {}
            if ft.get("home") is None or ft.get("away") is None:
                continue
            g["rm"] = ft["home"] if home else ft["away"]
            g["og"] = ft["away"] if home else ft["home"]
        elif m.get("status") in ("SCHEDULED", "TIMED", "IN_PLAY", "PAUSED", "LIVE"):
            pass
        else:
            continue
        games.append(g)
    games.sort(key=lambda x: x["t"])
    return games


def parse_table(payload):
    tables = payload.get("standings", [])
    best = None
    for s in tables:
        if s.get("type", "TOTAL") == "TOTAL" and s.get("table"):
            best = s["table"]
            break
    if not best:
        return []
    return [[r["position"], cz(r["team"]), r["won"], r["draw"], r["lost"], r["points"]] for r in best]


def spread(players):
    """Rozmístí hráče na stejné pozici vedle sebe."""
    groups = {}
    for p in players:
        groups.setdefault((p["x"], p["y"]), []).append(p)
    for (x, y), grp in groups.items():
        n = len(grp)
        if n == 1:
            continue
        for i, p in enumerate(grp):
            p["x"] = max(14, min(86, round(x + (i - (n - 1) / 2) * 26)))


def surname(full):
    parts = full.split()
    last = parts[-1] if parts else full
    return SURNAME_FIX.get(last, last)


def build_lineup(match, prev):
    side = "homeTeam" if match["homeTeam"]["id"] == TEAM_ID else "awayTeam"
    team = match[side]
    xi = team.get("lineup") or []
    bench = team.get("bench") or []
    if len(xi) != 11:
        return None
    nums = {}
    if prev:
        for n, name, *_ in prev.get("xi", []):
            nums[name] = n
        for n, name in prev.get("subs", []):
            nums[name] = n
    players = []
    for p in xi:
        x, y = POS.get(p.get("position") or "", (50, 50))
        name = surname(p["name"])
        players.append({"n": p.get("shirtNumber") or nums.get(name), "name": name, "x": x, "y": y})
    spread(players)
    ft = (match.get("score") or {}).get("fullTime") or {}
    return {
        "label": "%s – %s %s:%s" % (cz(match["homeTeam"]), cz(match["awayTeam"]), ft.get("home", "?"), ft.get("away", "?")),
        "date": match["utcDate"][:10],
        "xi": [[p["n"] if p["n"] is not None else "", p["name"], p["x"], p["y"]] for p in players],
        "subs": [[b.get("shirtNumber"), b["name"]] for b in bench[:5]],
    }


def average(prices):
    return round(statistics.mean(prices), 2)


def parse_odds(events):
    """Vrací {datum: {h,d,a,src}} pro zápasy Realu Madrid (h = domácí, a = hosté)."""
    out = {}
    for ev in events:
        if "Real Madrid" not in (ev.get("home_team", "") + ev.get("away_team", "")):
            continue
        hs, ds, as_ = [], [], []
        used = []
        for b in ev.get("bookmakers", []):
            for mk in b.get("markets", []):
                if mk.get("key") != "h2h":
                    continue
                price = {o["name"]: o["price"] for o in mk.get("outcomes", [])}
                if ev["home_team"] in price and ev["away_team"] in price and "Draw" in price:
                    hs.append(price[ev["home_team"]]); ds.append(price["Draw"]); as_.append(price[ev["away_team"]])
                    used.append(b.get("title", b.get("key")))
        if not hs:
            continue
        out[ev["commence_time"][:10]] = {
            "h": average(hs), "d": average(ds), "a": average(as_),
            "src": "průměr %d sázkových kanceláří (The Odds API)" % len(hs),
        }
    return out


def main():
    want_odds = "--odds" in sys.argv
    if not FD_KEY:
        print("Chybí FOOTBALL_DATA_KEY (secret v repozitáři).")
        return 1
    prev = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}
    data = dict(prev)
    errors = []

    # 1) zápasy
    try:
        games = parse_matches(fd("teams/%d/matches?season=%s&limit=100" % (TEAM_ID, SEASON)))
        if games:
            old_odds = {g["t"][:10]: g["odds"] for g in prev.get("games", []) if g.get("odds")}
            for g in games:
                if "rm" not in g and g["t"][:10] in old_odds:
                    g["odds"] = old_odds[g["t"][:10]]
            data["games"] = games
    except Exception as e:
        errors.append("zápasy: %s" % e)

    # 2) tabulky
    tables = dict(prev.get("tables", {}))
    for key, code in (("liga", "PD"), ("lm", "CL")):
        try:
            t = parse_table(fd("competitions/%s/standings?season=%s" % (code, SEASON)))
            if len(t) > 5:
                tables[key] = t
        except Exception as e:
            errors.append("tabulka %s: %s" % (code, e))
    data["tables"] = tables

    # 3) sestava z posledního odehraného zápasu
    try:
        played = [g for g in data.get("games", []) if "rm" in g and g.get("id")]
        if played:
            last = max(played, key=lambda g: g["t"])
            if not prev.get("lineup") or prev["lineup"].get("date") != last["t"][:10]:
                lu = build_lineup(fd("matches/%s" % last["id"]), prev.get("lineup"))
                if lu:
                    data["lineup"] = lu
    except Exception as e:
        errors.append("sestava: %s" % e)

    # 4) kurzy (jen s --odds, šetří měsíční limit)
    if want_odds and ODDS_KEY:
        odds = {}
        for sport in ("soccer_spain_la_liga", "soccer_uefa_champs_league"):
            try:
                url = "https://api.the-odds-api.com/v4/sports/%s/odds/?%s" % (
                    sport, urllib.parse.urlencode({"apiKey": ODDS_KEY, "regions": "eu", "markets": "h2h", "oddsFormat": "decimal"}))
                odds.update(parse_odds(http_json(url)))
            except Exception as e:
                errors.append("kurzy %s: %s" % (sport, e))
        for g in data.get("games", []):
            if "rm" not in g and g["t"][:10] in odds:
                g["odds"] = odds[g["t"][:10]]
                g.pop("prob", None)

    data["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["source"] = "football-data.org" + (", The Odds API" if want_odds and ODDS_KEY else "")
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for e in errors:
        print("VAROVÁNÍ:", e)
    print("Hotovo, zápasů: %d" % len(data.get("games", [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
