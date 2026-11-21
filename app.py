"""TFTIQ - Exposing hidden player builds.

Run:  python -m streamlit run app.py
Key:  set RIOT_API_KEY as an environment variable, or in .streamlit/secrets.toml:
          RIOT_API_KEY = "RGAPI-..."
      (or paste it into the sidebar). Never commit a real key.
"""
import os
import re
import time
from urllib.parse import quote

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

st.set_page_config(page_title="TFTIQ", page_icon="🌀", layout="wide")

# Riot routing: match data uses the regional cluster. SEA has no account-v1
# endpoint, so account lookups for SEA go through "asia".
REGIONS = ["americas", "europe", "asia", "sea"]
ACCOUNT_CLUSTER = {"americas": "americas", "europe": "europe", "asia": "asia", "sea": "asia"}

QUEUES = {1090: "Normal", 1100: "Ranked", 1130: "Hyper Roll", 1160: "Double Up"}


# ---------------------------------------------------------------- helpers
def load_key() -> str:
    try:
        return st.secrets["RIOT_API_KEY"]
    except Exception:
        return os.environ.get("RIOT_API_KEY", "")


def clean(name: str) -> str:
    """TFT14_Augment_SomeName -> 'Some Name'."""
    name = re.sub(r"^TFT\d*_(?:Item_|Augment_)?", "", name or "")
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).replace("_", " ").strip()


class RiotError(Exception):
    pass


def riot_get(url: str, api_key: str, retries: int = 2):
    for attempt in range(retries + 1):
        r = requests.get(url, headers={"X-Riot-Token": api_key}, timeout=10)
        if r.status_code == 429 and attempt < retries:
            try:
                wait = int(r.headers.get("Retry-After", "2"))
            except ValueError:
                wait = 2
            time.sleep(min(wait, 10))
            continue
        break
    if r.status_code == 200:
        return r.json()
    messages = {
        400: "Bad request. Check the Riot ID and tagline.",
        401: "No API key was sent.",
        403: "The API key is invalid or expired. Development keys expire every 24 hours.",
        404: "Not found. Check the Riot ID, tagline and region.",
        429: "Riot's rate limit was hit. Wait a minute and try again.",
    }
    raise RiotError(messages.get(r.status_code, f"Riot API returned status {r.status_code}."))


@st.cache_data(ttl=600, show_spinner=False)
def get_puuid(name: str, tag: str, region: str, api_key: str) -> str:
    host = ACCOUNT_CLUSTER[region]
    url = (f"https://{host}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/"
           f"{quote(name)}/{quote(tag)}")
    return riot_get(url, api_key)["puuid"]


@st.cache_data(ttl=300, show_spinner=False)
def get_match_ids(puuid: str, count: int, region: str, api_key: str) -> list:
    url = (f"https://{region}.api.riotgames.com/tft/match/v1/matches/by-puuid/"
           f"{puuid}/ids?start=0&count={count}")
    return riot_get(url, api_key)


@st.cache_data(ttl=86400, show_spinner=False)  # matches never change
def get_match_row(match_id: str, puuid: str, region: str, api_key: str):
    url = f"https://{region}.api.riotgames.com/tft/match/v1/matches/{match_id}"
    data = riot_get(url, api_key)
    info = data.get("info", {})
    for p in info.get("participants", []):
        if p.get("puuid") != puuid:
            continue
        active = sorted(
            (t for t in p.get("traits", []) if t.get("style", 0) > 0),
            key=lambda t: (t.get("style", 0), t.get("num_units", 0)),
            reverse=True,
        )
        return {
            "match_id": match_id,
            "queue": QUEUES.get(info.get("queue_id"), f"Other ({info.get('queue_id')})"),
            "set": info.get("tft_set_number"),
            "placement": p.get("placement"),
            "level": p.get("level"),
            "gold_left": p.get("gold_left"),
            "last_round": p.get("last_round"),
            "build": " / ".join(clean(t["name"]) for t in active[:3]) or "No active traits",
            "traits": [clean(t["name"]) for t in active],
            "units": [
                {"unit": clean(u.get("character_id", "")), "tier": u.get("tier"),
                 "items": [clean(i) for i in u.get("itemNames", [])]}
                for u in p.get("units", [])
            ],
            "augments": [clean(a) for a in p.get("augments", [])],
        }
    return None


def summarize(frame: pd.DataFrame, key: str) -> pd.DataFrame:
    g = frame.groupby(key)["placement"]
    out = pd.DataFrame({
        "Games": g.size(),
        "Avg placement": g.mean().round(2),
        "Top 4 rate %": (g.apply(lambda s: (s <= 4).mean()) * 100).round(1),
    })
    return out.sort_values(["Games", "Avg placement"], ascending=[False, True]).reset_index()


# ---------------------------------------------------------------- sidebar
st.title("🌀 TFTIQ")
st.caption("Exposing hidden player builds.")

with st.sidebar:
    st.header("Player")
    region = st.selectbox("Region", REGIONS, help="Use the region the player's server belongs to.")
    riot_id = st.text_input("Riot ID (game name)", value="")
    tag_line = st.text_input("Tagline", value="NA1")
    match_count = st.slider("Matches to pull", 5, 20, 10)
    api_key = load_key() or st.text_input("Riot API key", type="password")
    run = st.button("Analyze", type="primary", use_container_width=True)

# ---------------------------------------------------------------- fetch
if run:
    if not api_key:
        st.error("Add a Riot API key (RIOT_API_KEY or the sidebar field).")
    elif not riot_id.strip():
        st.error("Enter a Riot ID.")
    else:
        try:
            with st.spinner("Fetching matches from Riot…"):
                puuid = get_puuid(riot_id.strip(), tag_line.strip().lstrip("#"), region, api_key)
                ids = get_match_ids(puuid, match_count, region, api_key)
                if not ids:
                    st.warning("No recent TFT matches found for this account.")
                    st.session_state.pop("df", None)
                else:
                    rows = [get_match_row(m, puuid, region, api_key) for m in ids]
                    rows = [r for r in rows if r]
                    # Riot returns newest first; flip so charts read oldest -> newest.
                    st.session_state["df"] = pd.DataFrame(rows).iloc[::-1].reset_index(drop=True)
                    st.session_state["player"] = f"{riot_id.strip()} #{tag_line.strip().lstrip('#')}"
        except RiotError as e:
            st.error(str(e))
        except requests.RequestException:
            st.error("Couldn't reach Riot. Check your connection and try again.")

df = st.session_state.get("df")
if df is None or df.empty:
    st.info("Enter a Riot ID in the sidebar and press Analyze.")
    st.stop()

# ---------------------------------------------------------------- filters
queues = sorted(df["queue"].unique())
default = [q for q in queues if q == "Ranked"] or queues
picked = st.multiselect("Queues to include", queues, default=default)
df = df[df["queue"].isin(picked)].reset_index(drop=True)
if df.empty:
    st.warning("No games in the selected queues.")
    st.stop()
df["game"] = df.index + 1

st.subheader(st.session_state.get("player", ""))
if "Double Up" in picked:
    st.caption("Double Up placements are by team (1–4), so top 4 rate isn't meaningful for those games.")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Games", len(df))
m2.metric("Top 4 rate", f"{(df['placement'] <= 4).mean() * 100:.1f}%")
m3.metric("Win rate (1st)", f"{(df['placement'] == 1).mean() * 100:.1f}%")
m4.metric("Avg placement", f"{df['placement'].mean():.2f}")

tab_over, tab_builds, tab_units, tab_aug, tab_log = st.tabs(
    ["Overview", "Builds", "Units and items", "Augments", "Match log"])

# ---------------------------------------------------------------- tabs
with tab_over:
    fig = px.line(df, x="game", y="placement", markers=True, hover_data=["build", "queue"],
                  labels={"game": "Game (oldest to newest)", "placement": "Placement"})
    fig.update_yaxes(autorange="reversed", tickmode="linear", tick0=1, dtick=1)
    st.plotly_chart(fig, use_container_width=True)

    trait_counts = pd.Series([t for ts in df["traits"] for t in ts]).value_counts().head(10)
    if not trait_counts.empty:
        st.subheader("Most-played active traits")
        tc = trait_counts.reset_index()
        tc.columns = ["Trait", "Games"]
        st.plotly_chart(px.bar(tc, x="Games", y="Trait", orientation="h")
                        .update_yaxes(autorange="reversed"), use_container_width=True)

with tab_builds:
    st.caption("A build is the player's top three active traits in a game. "
               "Repeated builds with good results are the ones worth scouting.")
    builds = summarize(df, "build")
    st.dataframe(builds, use_container_width=True, hide_index=True)

unit_rows = [{"game": g, "placement": pl, "unit": u["unit"], "tier": u["tier"], "items": u["items"]}
             for g, pl, us in zip(df["game"], df["placement"], df["units"]) for u in us]
units = pd.DataFrame(unit_rows)

with tab_units:
    if units.empty:
        st.info("No unit data in these matches.")
    else:
        st.subheader("Units played")
        st.dataframe(summarize(units, "unit"), use_container_width=True, hide_index=True)
        items = units.explode("items").dropna(subset=["items"])
        if not items.empty:
            st.subheader("Most common unit and item pairings")
            pairs = (items.groupby(["unit", "items"]).agg(Games=("game", "nunique"),
                                                         **{"Avg placement": ("placement", "mean")})
                     .round(2).sort_values("Games", ascending=False).head(20).reset_index()
                     .rename(columns={"items": "Item"}))
            st.dataframe(pairs, use_container_width=True, hide_index=True)

with tab_aug:
    aug_rows = [{"game": g, "placement": pl, "augment": a}
                for g, pl, augs in zip(df["game"], df["placement"], df["augments"]) for a in augs]
    if aug_rows:
        st.dataframe(summarize(pd.DataFrame(aug_rows), "augment"),
                     use_container_width=True, hide_index=True)
    else:
        st.info("No augment data in these matches.")

with tab_log:
    st.dataframe(df[["game", "queue", "set", "placement", "level", "gold_left", "last_round", "build"]],
                 use_container_width=True, hide_index=True)
