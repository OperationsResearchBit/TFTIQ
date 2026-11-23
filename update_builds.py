"""Builds builds.json from recent high-rank TFT games. Run by GitHub Actions daily.
Needs env RIOT_API_KEY. Optional: PLATFORM (default na1), REGION (default americas)."""
import json, os, re, sys, time, datetime
from collections import Counter, defaultdict
from statistics import mean, median
import requests

KEY = os.environ["RIOT_API_KEY"]
PLATFORM = os.environ.get("PLATFORM", "na1")
REGION = os.environ.get("REGION", "americas")
PLAYERS, PER_PLAYER, MIN_GAMES = 100, 10, 20
MIN_HIDDEN_GAMES, MAX_RATE = 100, 3.0   # hidden pick: enough games, rarely played, top 4 clearly above 50%
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

def wilson_lo(k, n, z=1.96):
    """Lower bound of the 95% confidence interval for a rate k/n."""
    if n == 0: return 0.0
    p = k / n
    return (p + z*z/(2*n) - z*((p*(1-p) + z*z/(4*n)) / n) ** 0.5) / (1 + z*z/n)

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
            (p["placement"], p["level"], [(clean(u["character_id"]), [clean(i) for i in u.get("itemNames", [])]) for u in p["units"]]))

total = sum(len(r) for r in groups.values())
builds = []
for name, rows in groups.items():
    if len(rows) < MIN_GAMES: continue
    avg = mean(r[0] for r in rows)
    lvl = median(r[1] for r in rows)
    uc = Counter(n for r in rows for n in {u[0] for u in r[2]})
    ic = defaultdict(Counter)
    for r in rows:
        for n, its in r[2]:
            ic[n].update(its)
    units = [{"name": n, "items": [i for i, _ in ic[n].most_common(3)]} for n, _ in uc.most_common(8)]
    tier = "S" if avg <= 4.0 else "A" if avg <= 4.3 else "B" if avg <= 4.6 else "C"
    t4 = sum(r[0] <= 4 for r in rows)
    rate = 100 * len(rows) / total
    lo = wilson_lo(t4, len(rows))
    hidden = len(rows) >= MIN_HIDDEN_GAMES and rate < MAX_RATE and lo > 0.5
    builds.append({"name": name, "tier": tier, "hidden": hidden, "rate": round(rate, 1), "top4_lo": round(100*lo),
                   "style": "Fast 9" if lvl >= 9 else "Fast 8" if lvl >= 8 else "Reroll",
                   "avg": round(avg, 2), "top4": round(100 * sum(r[0] <= 4 for r in rows) / len(rows)),
                   "games": len(rows), "units": units,
                   "how": f"Seen {len(rows)} times in high-rank games, usually finishing around level {int(lvl)}. Most common units: {', '.join(u['name'] for u in units[:5])}."})
builds.sort(key=lambda b: b["avg"])
if not builds: sys.exit("No builds met the minimum sample size; keeping the old file.")

out = {"updated": datetime.date.today().isoformat(), "set": sets.most_common(1)[0][0] if sets else None,
       "source": f"Challenger games sampled from {PLATFORM.upper()} via the Riot API", "builds": builds}
json.dump(out, open("builds.json", "w"), indent=1)
print("wrote", len(builds), "builds")
