import streamlit as st
import fastf1
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import os
import json
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

# --- FastF1 Cache ---
if not os.path.exists("cache"):
    os.makedirs("cache")
fastf1.Cache.enable_cache("cache")

# --- Page Config ---
st.set_page_config(page_title="F1 Chat", page_icon="🏎️💬", layout="wide")

# --- Team Colors ---
FALLBACK_TEAM_COLORS = {
    "Red Bull": "#3671C6", "Mercedes": "#27F4D2", "Ferrari": "#E80020",
    "McLaren": "#FF8000", "Aston Martin": "#229971", "Alpine": "#0093cc",
    "Williams": "#37BEDD", "RB": "#6692FF", "Racing Bulls": "#6692FF", 
    "Sauber": "#52E252", "Haas": "#E6002B", "Audi": "#F50537", 
    "Cadillac": "#FFCC00"
}

def get_team_color(team_name):
    for k, v in FALLBACK_TEAM_COLORS.items():
        if k.lower() in team_name.lower():
            return v
    return "#CCCCCC"

def format_f1_time(td_str, is_delta, status=""):
    """Format pandas Timedelta string to F1 standard (e.g. +2.974s or 1:23.001)"""
    if pd.isna(td_str) or td_str == "NaT" or str(td_str).strip() == "":
        return status if pd.notna(status) else ""
        
    s_status = str(status) if pd.notna(status) else ""
    if s_status and s_status.lower() not in ["finished"]:
        if s_status.startswith("+"):
            return s_status
        if s_status.lower() == "lapped":
            return "+1 Lap"
        return s_status

    s = str(td_str)
    if "days" in s:
        s = s.split("days")[1].strip()
        
    parts = s.split(":")
    if len(parts) == 3:
        try:
            h = int(parts[0])
            m = int(parts[1])
            sec_f = float(parts[2])
            
            if h == 0 and m == 0:
                formatted = f"{sec_f:.3f}"
            elif h == 0:
                formatted = f"{m}:{sec_f:06.3f}"
            else:
                formatted = f"{h}:{m:02d}:{sec_f:06.3f}"
                
            if is_delta:
                return f"+{formatted}s"
            else:
                return formatted
        except ValueError:
            return s
            
    return s

# ─────────────────────────────────────────────
# DATA FUNCTIONS (called by Gemini via function calling)
# ─────────────────────────────────────────────

def get_schedule(year: int) -> dict:
    """Get the Formula 1 race schedule/calendar for a given year.

    Args:
        year: The season year (e.g. 2024).

    Returns:
        A dict with 'type' set to 'table' and 'data' containing the schedule.
    """
    try:
        schedule = fastf1.get_event_schedule(year)
        schedule = schedule[schedule["EventFormat"] != "testing"]
        cols = ["RoundNumber", "EventName", "Country", "Location", "EventDate", "EventFormat"]
        avail = [c for c in cols if c in schedule.columns]
        df = schedule[avail].copy()
        df["EventDate"] = df["EventDate"].astype(str)
        return {"type": "table", "title": f"{year} F1 Race Schedule", "data": df.to_dict(orient="records")}
    except Exception as e:
        return {"type": "error", "message": str(e)}


def get_race_results(year: int, grand_prix: str) -> dict:
    """Get the race results for a specific Grand Prix.

    Args:
        year: The season year (e.g. 2024).
        grand_prix: Name of the Grand Prix (e.g. 'Monaco', 'Silverstone', 'Monza').

    Returns:
        A dict with 'type' set to 'table' and 'data' containing the results.
    """
    try:
        session = fastf1.get_session(year, grand_prix, "R")
        session.load()
        results = session.results
        cols = ["Position", "BroadcastName", "Abbreviation", "TeamName", "Time", "Status", "Points"]
        avail = [c for c in cols if c in results.columns]
        df = results[avail].copy()
        if "Time" in df.columns:
            formatted_times = []
            for idx, row in df.iterrows():
                is_winner = (row.get("Position") in [1, 1.0, "1", "1.0"])
                formatted_times.append(format_f1_time(row["Time"], is_delta=not is_winner, status=row.get("Status", "")))
            df["Time"] = formatted_times
        if "Position" in df.columns:
            df["Position"] = df["Position"].astype(str)
        if "Points" in df.columns:
            df["Points"] = df["Points"].astype(str)
        return {"type": "table", "title": f"{year} {grand_prix} — Race Results", "data": df.to_dict(orient="records")}
    except Exception as e:
        return {"type": "error", "message": str(e)}


def get_qualifying_results(year: int, grand_prix: str) -> dict:
    """Get the qualifying results for a specific Grand Prix.

    Args:
        year: The season year (e.g. 2024).
        grand_prix: Name of the Grand Prix (e.g. 'Monaco', 'Silverstone').

    Returns:
        A dict with 'type' set to 'table' and 'data' containing the qualifying results.
    """
    try:
        session = fastf1.get_session(year, grand_prix, "Q")
        session.load()
        results = session.results
        cols = ["Position", "BroadcastName", "Abbreviation", "TeamName", "Q1", "Q2", "Q3"]
        avail = [c for c in cols if c in results.columns]
        df = results[avail].copy()
        
        for q_col in ["Q1", "Q2", "Q3"]:
            if q_col in df.columns:
                df[q_col] = df[q_col].apply(lambda x: format_f1_time(x, is_delta=False))
                
        for c in df.columns:
            if c not in ["Q1", "Q2", "Q3"]:
                df[c] = df[c].astype(str)
        return {"type": "table", "title": f"{year} {grand_prix} — Qualifying Results", "data": df.to_dict(orient="records")}
    except Exception as e:
        return {"type": "error", "message": str(e)}


def get_fastest_laps(year: int, grand_prix: str) -> dict:
    """Get the fastest lap for every driver in a race session.

    Args:
        year: The season year (e.g. 2024).
        grand_prix: Name of the Grand Prix (e.g. 'Monaco').

    Returns:
        A dict with 'type' set to 'chart' and chart data for fastest laps.
    """
    try:
        session = fastf1.get_session(year, grand_prix, "R")
        session.load()
        laps = session.laps
        drivers = session.results["Abbreviation"].dropna().tolist()
        fastest = []
        for d in drivers:
            try:
                d_laps = laps.pick_drivers(d)
                fl = d_laps.pick_fastest()
                if not pd.isna(fl["LapTime"]):
                    team = fl["Team"] if "Team" in fl else "Unknown"
                    fastest.append({
                        "Driver": d,
                        "LapTime_s": fl["LapTime"].total_seconds(),
                        "Team": team,
                        "Color": get_team_color(team),
                    })
            except Exception:
                pass
        if not fastest:
            return {"type": "error", "message": "No fastest lap data found."}
        return {
            "type": "chart",
            "chart_type": "bar",
            "title": f"{year} {grand_prix} — Fastest Lap per Driver",
            "data": sorted(fastest, key=lambda x: x["LapTime_s"]),
            "x": "Driver",
            "y": "LapTime_s",
            "color": "Team",
        }
    except Exception as e:
        return {"type": "error", "message": str(e)}


def get_lap_times(year: int, grand_prix: str, drivers: list[str]) -> dict:
    """Get lap-by-lap times for specific drivers in a race, useful for comparison.

    Args:
        year: The season year (e.g. 2024).
        grand_prix: Name of the Grand Prix (e.g. 'Monza').
        drivers: List of driver abbreviations to compare (e.g. ['VER', 'NOR']).

    Returns:
        A dict with 'type' set to 'chart' and lap time line chart data.
    """
    try:
        session = fastf1.get_session(year, grand_prix, "R")
        session.load()
        laps = session.laps
        all_data = []
        color_usage = {}
        
        for d in drivers:
            try:
                d_laps = laps.pick_drivers(d).copy()
                d_laps = d_laps.dropna(subset=["LapTime", "LapNumber"])
                d_laps["LapTime_s"] = d_laps["LapTime"].dt.total_seconds()
                d_laps = d_laps[d_laps["LapTime_s"] < d_laps["LapTime_s"].median() * 1.15]
                
                team = d_laps["Team"].iloc[0] if "Team" in d_laps and not d_laps["Team"].empty else "Unknown"
                color = get_team_color(team)
                
                ls_idx = color_usage.get(color, 0)
                DASH_STYLES = ["solid", "dash", "dot", "dashdot"]
                ls = DASH_STYLES[ls_idx % len(DASH_STYLES)]
                color_usage[color] = ls_idx + 1
                
                for _, row in d_laps.iterrows():
                    all_data.append({
                        "Driver": d,
                        "LapNumber": int(row["LapNumber"]),
                        "LapTime_s": row["LapTime_s"],
                        "Team": team,
                        "Color": color,
                        "LineStyle": ls,
                    })
            except Exception:
                pass
        if not all_data:
            return {"type": "error", "message": f"No lap data found for drivers: {drivers}"}
        return {
            "type": "chart",
            "chart_type": "line",
            "title": f"{year} {grand_prix} — Lap Times: {', '.join(drivers)}",
            "data": all_data,
            "x": "LapNumber",
            "y": "LapTime_s",
            "color": "Driver",
        }
    except Exception as e:
        return {"type": "error", "message": str(e)}


def get_speed_telemetry(year: int, grand_prix: str, driver1: str, driver2: str) -> dict:
    """Get speed vs distance telemetry comparison for two drivers on their fastest laps.

    Args:
        year: The season year (e.g. 2024).
        grand_prix: Name of the Grand Prix.
        driver1: First driver abbreviation (e.g. 'VER').
        driver2: Second driver abbreviation (e.g. 'HAM').

    Returns:
        A dict with 'type' set to 'chart' and telemetry chart data.
    """
    try:
        session = fastf1.get_session(year, grand_prix, "R")
        session.load()
        laps = session.laps
        d1_lap = laps.pick_drivers(driver1).pick_fastest()
        d2_lap = laps.pick_drivers(driver2).pick_fastest()
        d1_tel = d1_lap.get_telemetry()
        d2_tel = d2_lap.get_telemetry()
        
        t1 = d1_lap["Team"] if "Team" in d1_lap else "Unknown"
        t2 = d2_lap["Team"] if "Team" in d2_lap else "Unknown"
        c1 = get_team_color(t1)
        c2 = get_team_color(t2)
        
        ls1, ls2 = "solid", "solid"
        if c1 == c2:
            ls2 = "dash"
            
        data = []
        for _, row in d1_tel.iterrows():
            data.append({"Driver": driver1, "Distance": float(row["Distance"]), "Speed": float(row["Speed"]), "Team": t1, "Color": c1, "LineStyle": ls1})
        for _, row in d2_tel.iterrows():
            data.append({"Driver": driver2, "Distance": float(row["Distance"]), "Speed": float(row["Speed"]), "Team": t2, "Color": c2, "LineStyle": ls2})
        return {
            "type": "chart",
            "chart_type": "line",
            "title": f"{year} {grand_prix} — Speed Trace: {driver1} vs {driver2}",
            "data": data,
            "x": "Distance",
            "y": "Speed",
            "color": "Driver",
        }
    except Exception as e:
        return {"type": "error", "message": str(e)}


def get_driver_standings(year: int) -> dict:
    """Get the World Drivers' Championship standings for a season.

    Args:
        year: The season year (e.g. 2024).

    Returns:
        A dict with 'type' set to 'table' and standings data.
    """
    try:
        import requests
        url = f"https://api.jolpi.ca/ergast/f1/{year}/driverStandings/?format=json"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        standings_list = data["MRData"]["StandingsTable"]["StandingsLists"]
        if not standings_list:
            return {"type": "error", "message": f"No driver standings data for {year}."}
        standings = standings_list[0]["DriverStandings"]
        rows = []
        for s in standings:
            rows.append({
                "Pos": s["position"],
                "Driver": f"{s['Driver']['givenName']} {s['Driver']['familyName']}",
                "Team": s["Constructors"][0]["name"] if s["Constructors"] else "N/A",
                "Points": s["points"],
                "Wins": s["wins"],
            })
        return {"type": "table", "title": f"{year} Drivers' Championship Standings", "data": rows}
    except Exception as e:
        return {"type": "error", "message": str(e)}


def get_constructor_standings(year: int) -> dict:
    """Get the World Constructors' Championship standings for a season.

    Args:
        year: The season year (e.g. 2024).

    Returns:
        A dict with 'type' set to 'table' and standings data.
    """
    try:
        import requests
        url = f"https://api.jolpi.ca/ergast/f1/{year}/constructorStandings/?format=json"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        standings_list = data["MRData"]["StandingsTable"]["StandingsLists"]
        if not standings_list:
            return {"type": "error", "message": f"No constructor standings data for {year}."}
        standings = standings_list[0]["ConstructorStandings"]
        rows = []
        for s in standings:
            rows.append({
                "Pos": s["position"],
                "Constructor": s["Constructor"]["name"],
                "Points": s["points"],
                "Wins": s["wins"],
            })
        return {"type": "table", "title": f"{year} Constructors' Championship Standings", "data": rows}
    except Exception as e:
        return {"type": "error", "message": str(e)}


def get_season_results(year: int) -> dict:
    """Get the full race results for an entire season. Useful for calculating total laps, total wins, etc. for a driver or team across the year.

    Args:
        year: The season year (e.g. 2024).

    Returns:
        A dict with 'type' set to 'table' and season-long data.
    """
    try:
        import requests
        url = f"https://api.jolpi.ca/ergast/f1/{year}/results.json?limit=1000"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        races = data["MRData"]["RaceTable"]["Races"]
        rows = []
        for race in races:
            for res in race["Results"]:
                rows.append({
                    "GrandPrix": race["raceName"],
                    "Driver": res["Driver"]["code"] if "code" in res["Driver"] else res["Driver"]["familyName"],
                    "Team": res["Constructor"]["name"],
                    "Position": res["positionText"],
                    "Points": float(res["points"]),
                    "Laps": int(res["laps"]),
                    "Status": res["status"]
                })
        if not rows:
            return {"type": "error", "message": f"No season results found for {year}."}
        return {"type": "table", "title": f"{year} Full Season Results", "data": rows}
    except Exception as e:
        return {"type": "error", "message": str(e)}


def get_driver_season_summary(year: int, driver: str) -> dict:
    """Get a season summary for a specific driver, including total laps, points, and wins.

    Args:
        year: The season year (e.g. 2024).
        driver: The 3-letter driver abbreviation (e.g. 'NOR', 'VER').

    Returns:
        A dict with 'type' set to 'table' and the summary data.
    """
    try:
        import requests
        url = f"https://api.jolpi.ca/ergast/f1/{year}/results.json?limit=1000"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        races = data["MRData"]["RaceTable"]["Races"]
        
        total_laps = 0
        total_points = 0.0
        total_wins = 0
        races_entered = 0
        
        for race in races:
            for res in race["Results"]:
                res_driver_code = res["Driver"].get("code", "").upper()
                if res_driver_code == driver.upper() or res["Driver"]["familyName"].upper() == driver.upper():
                    races_entered += 1
                    total_laps += int(res["laps"])
                    total_points += float(res["points"])
                    if res["position"] == "1":
                        total_wins += 1
                        
        if races_entered == 0:
            return {"type": "error", "message": f"No data found for driver {driver} in {year}."}
            
        summary = [{
            "Driver": driver.upper(),
            "Year": year,
            "Races Entered": races_entered,
            "Total Laps": total_laps,
            "Total Points": total_points,
            "Wins": total_wins
        }]
        return {"type": "table", "title": f"{year} Season Summary: {driver.upper()}", "data": summary}
    except Exception as e:
        return {"type": "error", "message": str(e)}


def get_team_season_summary(year: int, team: str) -> dict:
    """Get a season summary for a specific constructor/team, including total laps, points, and wins.

    Args:
        year: The season year (e.g. 2024).
        team: The team name (e.g. 'McLaren', 'Ferrari').

    Returns:
        A dict with 'type' set to 'table' and the summary data.
    """
    try:
        import requests
        url = f"https://api.jolpi.ca/ergast/f1/{year}/results.json?limit=1000"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        races = data["MRData"]["RaceTable"]["Races"]
        
        total_laps = 0
        total_points = 0.0
        total_wins = 0
        races_entered = set()
        
        for race in races:
            for res in race["Results"]:
                if team.lower() in res["Constructor"]["name"].lower():
                    races_entered.add(race["raceName"])
                    total_laps += int(res["laps"])
                    total_points += float(res["points"])
                    if res["position"] == "1":
                        total_wins += 1
                        
        if not races_entered:
            return {"type": "error", "message": f"No data found for team {team} in {year}."}
            
        summary = [{
            "Team": team.title(),
            "Year": year,
            "Races Entered": len(races_entered),
            "Total Laps": total_laps,
            "Total Points": total_points,
            "Wins": total_wins
        }]
        return {"type": "table", "title": f"{year} Season Summary: {team.title()}", "data": summary}
    except Exception as e:
        return {"type": "error", "message": str(e)}


def get_season_driver_stats_chart(year: int, stat: str = "Laps") -> dict:
    """Get a bar chart showing the total Laps, Points, or Wins for every driver in a given season.

    Args:
        year: The season year (e.g. 2026).
        stat: The statistic to plot. Must be one of 'Laps', 'Points', or 'Wins'.

    Returns:
        A dict with 'type' set to 'chart' containing the data.
    """
    try:
        import requests
        url = f"https://api.jolpi.ca/ergast/f1/{year}/results.json?limit=1000"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        races = data["MRData"]["RaceTable"]["Races"]
        
        driver_stats = {}
        for race in races:
            for res in race["Results"]:
                driver_code = res["Driver"].get("code", res["Driver"]["familyName"]).upper()
                team = res["Constructor"]["name"]
                if driver_code not in driver_stats:
                    driver_stats[driver_code] = {"Driver": driver_code, "Laps": 0, "Points": 0.0, "Wins": 0, "Team": team}
                
                driver_stats[driver_code]["Laps"] += int(res["laps"])
                driver_stats[driver_code]["Points"] += float(res["points"])
                if res["position"] == "1":
                    driver_stats[driver_code]["Wins"] += 1
                    
        if not driver_stats:
            return {"type": "error", "message": f"No data found for {year}."}
            
        # Add colors
        stat_data = list(driver_stats.values())
        for d in stat_data:
            d["Color"] = get_team_color(d["Team"])

        # Sort by the requested stat
        valid_stat = stat if stat in ["Laps", "Points", "Wins"] else "Laps"
        stat_data = sorted(stat_data, key=lambda x: x[valid_stat], reverse=False)

        return {
            "type": "chart",
            "chart_type": "bar",
            "title": f"{year} Season Total {valid_stat} per Driver",
            "data": stat_data,
            "x": "Driver",
            "y": valid_stat,
            "color": "Team"
        }
    except Exception as e:
        return {"type": "error", "message": str(e)}


def get_preseason_testing_summary(year: int, by_team: bool = False) -> dict:
    """Get the total laps and fastest lap during all days of pre-season testing.

    Args:
        year: The season year (e.g. 2026).
        by_team: True to aggregate results by constructor team instead of driver.

    Returns:
        A dict with 'type' set to 'chart' containing a bar chart of total pre-season laps.
    """
    try:
        driver_stats = {}
        # Pre-season testing can occur across multiple tests (Test 1, Test 2) and days (Sessions).
        for test_num in [1, 2, 3]:
            for session_num in range(1, 10):
                try:
                    session = fastf1.get_testing_session(year, test_num, session_num)
                    session.load(telemetry=False, weather=False, messages=False)
                    laps = session.laps
                    
                    for d in laps["Driver"].unique():
                        d_laps = laps.pick_drivers(d)
                        team = d_laps["Team"].iloc[0] if "Team" in d_laps and not d_laps["Team"].empty else "Unknown"
                        
                        key = team if by_team else d
                        
                        if key not in driver_stats:
                            if by_team:
                                driver_stats[key] = {"Team": key, "Laps": 0}
                            else:
                                driver_stats[key] = {"Driver": key, "Team": team, "Laps": 0, "FastestLap": float('inf')}
                                
                        driver_stats[key]["Laps"] += len(d_laps)
                        
                        if not by_team:
                            try:
                                fl = d_laps.pick_fastest()
                                if not pd.isna(fl["LapTime"]):
                                    lap_time_s = fl["LapTime"].total_seconds()
                                    if lap_time_s < driver_stats[key]["FastestLap"]:
                                        driver_stats[key]["FastestLap"] = lap_time_s
                            except Exception:
                                pass
                                
                except ValueError:
                    break # No more sessions for this test_num, move to next test
                except Exception:
                    continue # Skip if data is corrupted but session exists

                
        if not driver_stats:
            return {"type": "error", "message": f"No pre-season testing data found for {year}."}
            
        stat_list = list(driver_stats.values())
        for d in stat_list:
            if not by_team and d["FastestLap"] == float('inf'):
                d["FastestLap"] = None
            d["Color"] = get_team_color(d["Team"])
            
        stat_list.sort(key=lambda x: x["Laps"], reverse=False)
        
        return {
            "type": "chart",
            "chart_type": "bar",
            "title": f"{year} Pre-Season Testing Total Laps by {'Team' if by_team else 'Driver'}",
            "data": stat_list,
            "x": "Team" if by_team else "Driver",
            "y": "Laps",
            "color": "Team"
        }
    except Exception as e:
        return {"type": "error", "message": str(e)}


def get_lap_positions(year: int, grand_prix: str) -> dict:
    """Get the track position of every driver at the end of each lap in a race.

    Args:
        year: The season year (e.g. 2026).
        grand_prix: Name of the Grand Prix (e.g. 'Monaco', 'Silverstone').

    Returns:
        A dict with 'type' set to 'chart' containing a line chart of lap positions.
    """
    try:
        session = fastf1.get_session(year, grand_prix, "R")
        session.load(telemetry=False, weather=False, messages=False)
        laps = session.laps
        
        cols = ["LapNumber", "Position", "Driver", "Team"]
        avail = [c for c in cols if c in laps.columns]
        df = laps[avail].copy()
        df = df.dropna(subset=["Position"])
        
        df["Color"] = df["Team"].apply(get_team_color)
        
        return {
            "type": "chart",
            "chart_type": "line",
            "title": f"{year} {grand_prix} — Lap Positions Chart",
            "data": df.to_dict(orient="records"),
            "x": "LapNumber",
            "y": "Position",
            "color": "Driver"
        }
    except Exception as e:
        return {"type": "error", "message": str(e)}


# ─────────────────────────────────────────────
# FUNCTION REGISTRY — maps names to callables
# ─────────────────────────────────────────────

FUNC_MAP = {
    "get_schedule": get_schedule,
    "get_race_results": get_race_results,
    "get_qualifying_results": get_qualifying_results,
    "get_fastest_laps": get_fastest_laps,
    "get_lap_times": get_lap_times,
    "get_speed_telemetry": get_speed_telemetry,
    "get_driver_standings": get_driver_standings,
    "get_constructor_standings": get_constructor_standings,
    "get_season_results": get_season_results,
    "get_driver_season_summary": get_driver_season_summary,
    "get_team_season_summary": get_team_season_summary,
    "get_season_driver_stats_chart": get_season_driver_stats_chart,
    "get_preseason_testing_summary": get_preseason_testing_summary,
    "get_lap_positions": get_lap_positions,
}


# ─────────────────────────────────────────────
# DISPLAY HELPERS
# ─────────────────────────────────────────────

def render_result(result: dict, user_prompt: str = "", key_prefix: str = ""):
    """Render a data result as a chart or table inside a Streamlit chat message."""
    if result["type"] == "error":
        st.error(f"⚠️ {result['message']}")
        return

    st.markdown(f"**{result.get('title', '')}**")

    # Force table visualization if the user explicitly asked for a table
    if "table" in user_prompt.lower() and result["type"] == "chart":
        result["type"] = "table"

    if result["type"] == "table":
        df = pd.DataFrame(result["data"])
        
        # Clean up internal charting columns if present
        for col in ["Color", "LineStyle", "x", "y"]:
            if col in df.columns:
                df = df.drop(columns=[col])
                
        # Friendly column names
        rename_map = {
            "LapTime_s": "Lap Time (s)",
            "LapNumber": "Lap",
            "Distance": "Distance (m)",
            "Speed": "Speed (km/h)",
            "Position": "Pos",
            "Points": "Pts"
        }
        df = df.rename(columns=rename_map)
        
        st.dataframe(df, use_container_width=True)

    elif result["type"] == "chart":
        df = pd.DataFrame(result["data"])
        chart_type = result.get("chart_type", "bar")
        color_col = result.get("color", None)

        # Build a color map from team colors if available
        color_map = None
        if color_col and "Color" in df.columns:
            color_map = dict(zip(df[color_col], df["Color"]))
        elif color_col:
            unique_vals = df[color_col].unique()
            palette = px.colors.qualitative.Bold
            color_map = {v: palette[i % len(palette)] for i, v in enumerate(unique_vals)}

        if chart_type == "bar":
            fig = px.bar(df, x=result["x"], y=result["y"], color=color_col,
                         color_discrete_map=color_map, title=result["title"])
            if result["y"] == "LapTime_s":
                min_t, max_t = df[result["y"]].min() * 0.98, df[result["y"]].max() * 1.02
                fig.update_layout(yaxis=dict(range=[min_t, max_t]))
        elif chart_type == "line":
            if "LineStyle" in df.columns:
                fig = px.line(df, x=result["x"], y=result["y"], color="Driver",
                              line_dash="LineStyle", line_dash_map={"solid": "solid", "dash": "dash", "dot": "dot", "dashdot": "dashdot"},
                              color_discrete_map=color_map, title=result["title"])
            else:
                fig = px.line(df, x=result["x"], y=result["y"], color="Driver",
                              color_discrete_map=color_map, title=result["title"])
                              
            if result["y"] == "Position":
                fig.update_layout(yaxis=dict(autorange="reversed"))
        else:
            fig = px.scatter(df, x=result["x"], y=result["y"], color=color_col,
                             color_discrete_map=color_map, title=result["title"])

        fig.update_layout(template="plotly_dark")
        
        # Strip out any ", solid" or ", dash" tags from the legend names
        fig.for_each_trace(lambda t: t.update(name=t.name.split(",")[0] if t.name else t.name))
        
        st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_chart")


# ─────────────────────────────────────────────
# GEMINI CLIENT SETUP
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert Formula 1 data analyst chatbot. You help users explore F1 data by calling the available functions.

RULES:
- When a user asks about F1 data, YOU MUST call the appropriate function(s) to retrieve it. Do not rely on internal knowledge for stats.
- If the user asks a general F1 knowledge question (not needing data lookup), answer directly.
- For driver names, use the standard 3-letter abbreviations (VER, HAM, NOR, LEC, PIA, SAI, RUS, etc.).
- If the user mentions a driver's full name, convert it to the abbreviation.
- Grand Prix names should be the common short name (e.g. 'Monaco', 'Silverstone').
- The current year is 2026. If the user asks about the 2026 season, ALWAYS call the data functions.
- CRITICAL: If the user asks for a graph, chart, or visual of season-long driver stats, YOU ABSOLUTELY MUST CALL THE `get_season_driver_stats_chart` FUNCTION. DO NOT generate a text-based list or markdown representation of a chart. The function will directly render the chart in the UI.
- CRITICAL: DO NOT output any markdown tables of the raw data! The tools automatically render interactive tables and charts in the UI. You must only provide a brief 1-2 sentence text summary of the key findings, with no tabular data attached.
"""


def get_gemini_client(api_key: str):
    """Create and return a Gemini client."""
    return genai.Client(api_key=api_key)


def chat_with_gemini(client, messages: list, tools: list, tool_config=None):
    """Send messages to Gemini and handle function calling loop."""
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite-preview",
        contents=messages,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.2,
            tools=tools,
            tool_config=tool_config,
        ),
    )
    return response


# ─────────────────────────────────────────────
# STREAMLIT UI
# ─────────────────────────────────────────────

def main():
    # --- Header ---
    st.markdown("""
    <style>
        .main-header {
            background: linear-gradient(135deg, #e10600 0%, #1e1e1e 100%);
            padding: 1.5rem 2rem;
            border-radius: 12px;
            margin-bottom: 1.5rem;
        }
        .main-header h1 { color: white; margin: 0; font-size: 2rem; }
        .main-header p { color: #ccc; margin: 0.3rem 0 0 0; font-size: 1rem; }
        .stChatMessage { border-radius: 12px; }
    </style>
    <div class="main-header">
        <h1>🏎️💬 F1 Data Chat</h1>
        <p>Ask me anything about Formula 1 — results, standings, lap times, telemetry & more</p>
    </div>
    """, unsafe_allow_html=True)

    # --- Sidebar ---
    with st.sidebar:
        st.header("⚙️ Settings")
        api_key = st.text_input(
            "Gemini API Key",
            type="password",
            value=os.environ.get("GEMINI_API_KEY", ""),
            help="Get a free key at https://aistudio.google.com",
        )
        if api_key:
            st.success("API key set ✓")
        else:
            st.warning("Enter your Gemini API key to start chatting.")

        st.divider()
        st.markdown("### 💡 Try asking:")
        st.markdown("""
        - *Who won the 2024 Monaco GP?*
        - *Show me the qualifying results for Silverstone 2024*
        - *Compare lap times for VER vs NOR at Monza 2024*
        - *Show the 2024 drivers championship standings*
        - *What's the 2025 F1 schedule?*
        - *Compare speed telemetry of HAM vs LEC at Spa 2024*
        """)

        st.divider()
        if st.button("🗑️ Clear Chat", use_container_width=True):
            st.session_state.messages = []
            st.session_state.gemini_history = []
            st.rerun()

    # --- Chat State ---
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "gemini_history" not in st.session_state:
        st.session_state.gemini_history = []

    # --- Display chat history ---
    for i, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"], avatar="🏎️" if msg["role"] == "assistant" else None):
            if msg.get("text"):
                st.markdown(msg["text"])
            if msg.get("result"):
                render_result(msg["result"], msg.get("user_prompt", ""), key_prefix=f"msg_{i}")

    # --- Chat input ---
    if prompt := st.chat_input("Ask about F1 data...", disabled=not api_key):
        # Show the user message
        st.session_state.messages.append({"role": "user", "text": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # Check if user is asking for data to force the Lite model to use a tool
        is_data_request = any(w in prompt.lower() for w in ["graph", "chart", "plot", "table", "telemetry", "laps", "who", "what", "when", "results", "fastest", "standings", "schedule", "compare", "position"])

        # Build the tools list from our functions
        tool_functions = [
            get_schedule,
            get_race_results,
            get_qualifying_results,
            get_fastest_laps,
            get_lap_times,
            get_speed_telemetry,
            get_driver_standings,
            get_constructor_standings,
            get_season_results,
            get_driver_season_summary,
            get_team_season_summary,
            get_season_driver_stats_chart,
            get_preseason_testing_summary,
            get_lap_positions,
        ]

        # Build Gemini conversation history
        gemini_messages = st.session_state.gemini_history.copy()
        gemini_messages.append(types.Content(role="user", parts=[types.Part.from_text(text=prompt)]))

        with st.chat_message("assistant", avatar="🏎️"):
            with st.spinner("Thinking..."):
                try:
                    client = get_gemini_client(api_key)

                    # Dynamic tool config for Lite models
                    current_tool_config = None
                    if is_data_request:
                        current_tool_config = types.ToolConfig(
                            function_calling_config=types.FunctionCallingConfig(
                                mode="ANY"
                            )
                        )

                    # Function calling loop
                    max_iterations = 5
                    final_text = ""
                    data_results = []

                    for i in range(max_iterations):
                        # Force the tool config ONLY on the first iteration to prevent infinite looping
                        config_to_use = current_tool_config if i == 0 else None
                        
                        response = chat_with_gemini(
                            client,
                            gemini_messages,
                            tools=tool_functions,
                            tool_config=config_to_use,
                        )

                        # Check if model wants to call functions
                        has_function_call = False
                        function_response_parts = []

                        for part in response.candidates[0].content.parts:
                            if part.function_call:
                                has_function_call = True
                                fn_name = part.function_call.name
                                fn_args = dict(part.function_call.args) if part.function_call.args else {}

                                # Execute the function
                                if fn_name in FUNC_MAP:
                                    result = FUNC_MAP[fn_name](**fn_args)
                                    data_results.append(result)

                                    # Build function response for Gemini
                                    # Send up to 1000 rows so Gemini can actually aggregate full seasons
                                    if result["type"] == "table":
                                        summary = json.dumps(result["data"][:1000], default=str)
                                        if len(result["data"]) > 1000:
                                            summary += f"\n... and {len(result['data'])-1000} more rows"
                                    elif result["type"] == "chart":
                                        summary = json.dumps(result["data"][:50], default=str)
                                        if len(result["data"]) > 50:
                                            summary += f"\n... and {len(result['data'])-50} more data points"
                                    else:
                                        summary = json.dumps(result, default=str)

                                    function_response_parts.append(
                                        types.Part.from_function_response(
                                            name=fn_name,
                                            response={"result": summary},
                                        )
                                    )
                                else:
                                    function_response_parts.append(
                                        types.Part.from_function_response(
                                            name=fn_name,
                                            response={"error": f"Unknown function: {fn_name}"},
                                        )
                                    )

                        if has_function_call:
                            # Add the model's response and our function results to history
                            gemini_messages.append(response.candidates[0].content)
                            gemini_messages.append(
                                types.Content(role="user", parts=function_response_parts)
                            )
                            continue  # let model process the function results
                        else:
                            # Model gave a text response — we're done
                            for part in response.candidates[0].content.parts:
                                if part.text:
                                    final_text += part.text
                            break

                    # Render data results (charts / tables)
                    for idx, r in enumerate(data_results):
                        render_result(r, prompt, key_prefix=f"new_{idx}")

                    # Render text summary
                    if final_text:
                        st.markdown(final_text)

                    # Save to history
                    gemini_messages.append(response.candidates[0].content)
                    st.session_state.gemini_history = gemini_messages

                    # Save to display history
                    for r in data_results:
                        st.session_state.messages.append({"role": "assistant", "result": r, "user_prompt": prompt})
                    if final_text:
                        st.session_state.messages.append({"role": "assistant", "text": final_text})

                except Exception as e:
                    st.error(f"Error: {e}")
                    st.session_state.messages.append({"role": "assistant", "text": f"❌ Error: {e}"})


if __name__ == "__main__":
    main()
