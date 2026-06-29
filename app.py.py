
import streamlit as st
import requests
import pandas as pd
import plotly.express as px

# Configuration
API_KEY = "RGAPI-YOUR-KEY-HERE"  # Keep this secure in production!

# Regional Routing Rules
# Account queries use cluster-based routing; match queries use regional routing.
REGIONAL_ROUTING = "americas" # Options: americas, asia, europe, esports

st.set_page_config(page_title="TFT Analytics Dashboard", layout="wide")
st.title("📊 TFT Player Analytics Dashboard")

# 1. User Input Elements
col1, col2, col3 = st.columns([3, 1, 2])
with col1:
    riot_id = st.text_input("Riot ID (Game Name)", value="SummonerName")
with col2:
    tag_line = st.text_input("Tagline", value="NA1")
with col3:
    match_count = st.slider("Number of matches to pull", 5, 20, 10)

headers = {"X-Riot-Token": API_KEY}

# 2. Data Fetching Functions
def get_puuid(game_name, tag):
    """Converts a standard Riot ID into an immutable PUUID identifier."""
    url = f"https://{REGIONAL_ROUTING}://{game_name}/{tag}"
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json().get("puuid")
    return None

def get_match_ids(puuid, count):
    """Retrieves a historical list of recent TFT match tokens for the PUUID."""
    url = f"https://{REGIONAL_ROUTING}://{puuid}/ids?count={count}"
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json()
    return []

def get_match_details(match_id, target_puuid):
    """Fetches specific performance layout data for the player inside a match."""
    url = f"https://{REGIONAL_ROUTING}://{match_id}"
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        return None
    
    match_data = response.json()
    participants = match_data.get("info", {}).get("participants", [])
    
    # Locate target player footprint inside the 8-player match data payload
    for player in participants:
        if player.get("puuid") == target_puuid:
            return {
                "match_id": match_id,
                "placement": player.get("placement"),
                "gold_left": player.get("gold_left"),
                "last_round": player.get("last_round"),
                "level": player.get("level"),
                "traits": [t['name'] for t in player.get("traits", []) if t['tier_current'] > 0]
            }
    return None

# 3. Dashboard Execution Logic
if st.button("Analyze Match History"):
    with st.spinner("Fetching data from Riot Servers..."):
        puuid = get_puuid(riot_id, tag_line)
        
        if not puuid:
            st.error("Player profile not found. Verify Riot ID, Tagline, and Routing Region.")
        else:
            match_ids = get_match_ids(puuid, match_count)
            
            if not match_ids:
                st.warning("No recent TFT matches found for this account layout.")
            else:
                # Accumulate raw JSON statistics into a structural layout list
                parsed_history = []
                for m_id in match_ids:
                    details = get_match_details(m_id, puuid)
                    if details:
                        parsed_history.append(details)
                
                df = pd.DataFrame(parsed_history)
                
                if df.empty:
                    st.error("Failed to parse data fields from match responses.")
                else:
                    # 4. Metrics Processing
                    total_games = len(df)
                    top_4_counts = len(df[df['placement'] <= 4])
                    win_rate = (top_4_counts / total_games) * 100
                    avg_placement = df['placement'].mean()
                    
                    # Highlight Top-level KPIS
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Games Evaluated", f"{total_games}")
                    m2.metric("Top 4 Win Rate", f"{win_rate:.1f}%")
                    m3.metric("Avg Placement", f"{avg_placement:.2f}")
                    
                    # 5. Dashboard Data Visualizations
                    st.subheader("Placement Distribution History")
                    # Invert Y-axis because 1st place is visually higher than 8th place
                    fig_line = px.line(df, x="match_id", y="placement", markers=True, text="placement",
                                       labels={"placement": "Finish Position", "match_id": "Match Token ID"})
                    fig_line.update_yaxes(autorange="reversed", tickmode="linear", tick0=1, dtick=1)
                    st.plotly_chart(fig_line, use_container_width=True)
                    
                    # Data layout splits for granular insights
                    left_col, right_col = st.columns(2)
                    
                    with left_col:
                        st.subheader("Trait Frequency Metrics")
                        all_traits = [trait for sublist in df['traits'].tolist() for trait in sublist]
                        trait_counts = pd.Series(all_traits).value_counts().reset_index()
                        trait_counts.columns = ['Trait', 'Times Played']
                        
                        fig_bar = px.bar(trait_counts.head(8), x="Times Played", y="Trait", orientation='h',
                                         color="Times Played", color_continuous_scale="Viridis")
                        st.plotly_chart(fig_bar, use_container_width=True)
                        
                    with right_col:
                        st.subheader("Raw Match Performance Log")
                        st.dataframe(df[['placement', 'level', 'gold_left', 'last_round']], 
                                     use_container_width=True)
