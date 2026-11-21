"""Builds builds.json from recent high-rank TFT games. Run by GitHub Actions daily.
Needs env RIOT_API_KEY. Optional: PLATFORM (default na1), REGION (default americas)."""
import json, os, re, sys, time, datetime
from collections import Counter, defaultdict
from statistics import mean, median
import requests

KEY = os.environ["RIOT_API_KEY"]
PLATFORM = os.environ.get("PLATFORM", "na1")
REGION = os.environ.get("REGION", "americas")
PLAYERS, PER_PLAYER, MIN_GAMES = 40, 5, 20
H = {"X-Riot-Token": KEY}

def get(url):
    for _ in range(4):
        r = requests.get(url, headers=H, timeout=15)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", "5")) + 1); continue
        time.sleep(1.3)  # stay under 100 requests / 2 minutes
        if r.status_code == 200: return r.json()
        print("skip", r.status_code, url.split("?")[0][-60:]); return None
    return None

def clean(n):
    n = re.sub(r"^TFT\d*_(?:Item_|Augment_)?", "", n or "")
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", n).replace("_", " ").strip()

league = get(f"https://{PLATFORM}.api.riotgames.com/tft/league/v1/challenger?queue=RANKED_TFT")
if not league: sys.exit("Could not load the ladder. Check the key and PLATFORM.")
entries = sorted(league["entries"], key=lambda e: -e["leaguePoints"])[:PLAYERS]

match_ids = set()
for e in entries:
    puuid = e.get("puuid")
    if not puuid and e.get("summonerId"):
        s = get(f"https://{PLATFORM}.api.riotgames.com/tft/summoner/v1/summoners/{e['summonerId']}")
        puuid = s and s.get("puuid")
    if not puuid: continue
    ids = get(f"https://{REGION}.api.riotgames.com/tft/match/v1/matches/by-puuid/{puuid}/ids?count={PER_PLAYER}")
    match_ids.update(ids or [])

groups, sets = defaultdict(list), Counter()
for m in match_ids:
    d = get(f"https://{REGION}.api.riotgames.com/tft/match/v1/matches/{m}")
    if not d or d["info"].get("queue_id") != 1100: continue
    sets[d["info"].get("tft_set_number")] += 1
    for p in d["info"]["participants"]:
        act = sorted((t for t in p["traits"] if t.get("style", 0) > 0),
                     key=lambda t: (t["style"], t["num_units"]), reverse=True)[:3]
        if len(act) < 2: continue
        groups[" / ".join(clean(t["name"]) for t in act)].append(
            (p["placement"], p["level"], [clean(u["character_id"]) for u in p["units"]]))

builds = []
for name, rows in groups.items():
    if len(rows) < MIN_GAMES: continue
    avg = mean(r[0] for r in rows)
    lvl = median(r[1] for r in rows)
    uc = Counter(u for r in rows for u in set(r[2]))
    units = [u for u, _ in uc.most_common(8)]
    tier = "S" if avg <= 4.0 else "A" if avg <= 4.3 else "B" if avg <= 4.6 else "C"
    builds.append({"name": name, "tier": tier,
                   "style": "Fast 9" if lvl >= 9 else "Fast 8" if lvl >= 8 else "Reroll",
                   "avg": round(avg, 2), "top4": round(100 * sum(r[0] <= 4 for r in rows) / len(rows)),
                   "games": len(rows), "units": units,
                   "how": f"Seen {len(rows)} times in high-rank games, usually finishing around level {int(lvl)}. Most common units: {', '.join(units[:5])}."})
builds.sort(key=lambda b: b["avg"])
if not builds: sys.exit("No builds met the minimum sample size; keeping the old file.")

out = {"updated": datetime.date.today().isoformat(), "set": sets.most_common(1)[0][0] if sets else None,
       "source": f"Challenger games sampled from {PLATFORM.upper()} via the Riot API", "builds": builds}
json.dump(out, open("builds.json", "w"), indent=1)
print("wrote", len(builds), "builds")
