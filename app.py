import streamlit as st
import fastf1
import fastf1.plotting
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import os

# Create a cache directory for fastf1
if not os.path.exists("cache"):
    os.makedirs("cache")
fastf1.Cache.enable_cache("cache")

st.set_page_config(page_title="F1 Data Explorer V2", page_icon="🏎️", layout="wide")

st.title("Formula 1 Data Explorer V2 🏎️")
st.markdown("Explore race results, lap times, team pace, and telemetry data using FastF1.")

# Color helpers
FALLBACK_TEAM_COLORS = {
    "Red Bull": "#3671C6",
    "Mercedes": "#27F4D2",
    "Ferrari": "#E80020",
    "McLaren": "#FF8000",
    "Aston Martin": "#229971",
    "Alpine": "#0093cc",
    "Williams": "#37BEDD",
    "RB": "#6692FF",
    "Sauber": "#52E252",
    "Haas": "#B6BABD"
}

def get_team_color(team, session):
    try:
        return fastf1.plotting.get_team_color(team, session=session)
    except Exception:
        # Fallback for 2026 teams using substring mapping
        for k, v in FALLBACK_TEAM_COLORS.items():
            if k.lower() in team.lower():
                return v
        return "#CCCCCC"

def get_driver_color(driver, session):
    try:
        return fastf1.plotting.get_driver_color(driver, session=session)
    except Exception:
        # FastF1 2026 missing colors fallback: look up team from session results
        try:
            driver_info = session.results[session.results['Abbreviation'] == driver]
            if not driver_info.empty:
                team_name = driver_info['TeamName'].iloc[0]
                return get_team_color(team_name, session)
        except:
            pass
        return "#CCCCCC"

def driver_selector(view_key, available_drivers):
    """Render a driver multiselect with Select All and Clear All buttons."""
    state_key = f"sel_{view_key}"
    if state_key not in st.session_state:
        st.session_state[state_key] = available_drivers
    else:
        st.session_state[state_key] = [d for d in st.session_state[state_key] if d in available_drivers]

    col1, col2, _ = st.columns([2, 2, 8])
    if col1.button("Select All", key=f"all_{view_key}"):
        st.session_state[state_key] = available_drivers
    if col2.button("Clear All", key=f"none_{view_key}"):
        st.session_state[state_key] = []

    return st.multiselect("Filter Drivers:", available_drivers, key=state_key)


# --- Sidebar Filters ---
st.sidebar.header("Data Filters")
# Updating year to include current and past few years
year = st.sidebar.selectbox("Year", list(range(2026, 2017, -1)))

@st.cache_data
def get_schedule(year):
    try:
        return fastf1.get_event_schedule(year)
    except Exception as e:
        return None

schedule = get_schedule(year)

if schedule is None or schedule.empty:
    st.error(f"Could not load schedule for year {year}. Data may not be available yet.")
    st.stop()

# Filter schedule to include only past events, and sort them chronologically
now = pd.Timestamp.now(tz='UTC')
schedule['Session1Date'] = pd.to_datetime(schedule['Session1Date'], utc=True)
schedule = schedule[schedule['Session1Date'] <= now].sort_values(by='Session1Date')

# Drop testing events if possible
events = schedule[schedule['EventFormat'] != 'testing']['EventName'].tolist()

if not events:
    st.warning(f"No completed events found for {year} yet.")
    st.stop()

event = st.sidebar.selectbox("Grand Prix", events)

session_type_options = ["Race", "Qualifying", "Sprint", "Sprint Shootout", "Practice 1", "Practice 2", "Practice 3"]
session_type = st.sidebar.selectbox("Session", session_type_options)

session_map = {
    "Race": "R", "Qualifying": "Q", 
    "Sprint": "S", "Sprint Shootout": "SS", 
    "Practice 1": "FP1", "Practice 2": "FP2", "Practice 3": "FP3"
}
session_identifier = session_map[session_type]

# --- Load Data ---
@st.cache_data
def load_session_data(year, event, session_identifier):
    try:
        session = fastf1.get_session(year, event, session_identifier)
        session.load()
        return session
    except Exception as e:
        return str(e)

with st.spinner('Loading Session Data...'):
    session = load_session_data(year, event, session_identifier)

if isinstance(session, str):
    st.error(f"Failed to load data: {session}. The session might not have occurred yet.")
else:
    st.success(f"Loaded {year} {event} - {session_type}")
    
    # --- Results Table ---
    st.subheader("Session Results")
    try:
        results = session.results
        cols = ['Position', 'DriverNumber', 'BroadcastName', 'Abbreviation', 'TeamName', 'Time', 'Status', 'Points']
        avail_cols = [c for c in cols if c in results.columns]
        display_results = results[avail_cols].copy()
        
        if 'Time' in display_results.columns:
            display_results['Time'] = display_results['Time'].astype(str)
        st.dataframe(display_results, use_container_width=True)
    except Exception as e:
        st.warning(f"Could not load results table. Error: {e}")

    # --- Graphical Views ---
    st.header("Graphical Views (Advanced)")
    
    view_options = [
        "Fastest Lap Comparison", 
        "Telemetry (Speed vs Distance)",
        "Team Pace Comparison",
        "Position Changes (Race/Sprint)",
        "Lap Time Distribution"
    ]
    
    view_type = st.radio("Select View", view_options, horizontal=True)

    if 'Abbreviation' in results.columns:
        drivers = results['Abbreviation'].dropna().tolist()
    else:
        drivers = []

    laps = None
    try:
        laps = session.laps
    except:
        pass

    if view_type == "Fastest Lap Comparison" and drivers and laps is not None:
        st.subheader("Fastest Lap Times")
        selected_drivers = driver_selector("FastLap", drivers)
        
        if selected_drivers:
            fastest_laps = []
            for d in selected_drivers:
                try:
                    d_laps = laps.pick_driver(d)
                    fastest = d_laps.pick_fastest()
                    if not pd.isna(fastest['LapTime']):
                        fastest_laps.append({
                            "Driver": d,
                            "LapTime": fastest['LapTime'].total_seconds(),
                            "Team": fastest['Team'] if 'Team' in fastest else "Unknown"
                        })
                except:
                    pass
            
            if fastest_laps:
                df_fastest = pd.DataFrame(fastest_laps)
                team_colors = {t: get_team_color(t, session) for t in df_fastest['Team'].unique()}
                
                sort_by = st.radio("Sort By", ["Team", "Fastest Lap Time"], key="sort_fastlap", horizontal=True)
                if sort_by == "Fastest Lap Time":
                    df_fastest = df_fastest.sort_values(by="LapTime")
                else:
                    df_fastest = df_fastest.sort_values(by=["Team", "LapTime"])

                fig = px.bar(df_fastest, x="Driver", y="LapTime", color="Team", 
                             color_discrete_map=team_colors,
                             title="Fastest Lap Time per Driver (seconds)")
                fig.update_xaxes(categoryorder='array', categoryarray=df_fastest['Driver'])
                
                min_time = df_fastest['LapTime'].min() * 0.98
                max_time = df_fastest['LapTime'].max() * 1.02
                fig.update_layout(yaxis=dict(range=[min_time, max_time]))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("No valid fastest lap data found for selected drivers.")

    elif view_type == "Telemetry (Speed vs Distance)" and drivers and laps is not None:
        st.subheader("Telemetry Comparison")
        col1, col2 = st.columns(2)
        driver1 = col1.selectbox("Driver 1", drivers, index=0)
        driver2 = col2.selectbox("Driver 2", drivers, index=1 if len(drivers)>1 else 0)
        
        if driver1 and driver2:
            try:
                d1_lap = laps.pick_driver(driver1).pick_fastest()
                d2_lap = laps.pick_driver(driver2).pick_fastest()
                
                d1_tel = d1_lap.get_telemetry()
                d2_tel = d2_lap.get_telemetry()
                
                c1 = get_driver_color(driver1, session)
                c2 = get_driver_color(driver2, session)
                
                dash1, dash2 = 'solid', 'solid'
                if c1.lower() == c2.lower() and driver1 != driver2:
                    dash2 = 'dash'
                
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=d1_tel['Distance'], y=d1_tel['Speed'], name=f"{driver1} Speed", mode='lines', line=dict(color=c1, dash=dash1)))
                fig.add_trace(go.Scatter(x=d2_tel['Distance'], y=d2_tel['Speed'], name=f"{driver2} Speed", mode='lines', line=dict(color=c2, dash=dash2)))
                
                fig.update_layout(title="Speed vs Distance", xaxis_title="Distance (m)", yaxis_title="Speed (km/h)")
                st.plotly_chart(fig, use_container_width=True)
            except Exception as e:
                st.warning(f"Could not load telemetry for selected drivers. ({e})")
                
    elif view_type == "Team Pace Comparison" and laps is not None:
        st.subheader("Team Pace Comparison")
        st.markdown("Shows average lap pace for each team. Uses 'quick laps' (excludes in/out/slow laps).")
        try:
            quick_laps = laps.pick_quicklaps().copy()
            quick_laps['LapTime_s'] = quick_laps['LapTime'].dt.total_seconds()
            quick_laps = quick_laps.dropna(subset=['LapTime_s', 'Team'])
            
            if not quick_laps.empty:
                team_colors = {t: get_team_color(t, session) for t in quick_laps['Team'].unique()}
                sort_by = st.radio("Sort By", ["Alphabetical", "Median Lap Time"], key="sort_team_pace", horizontal=True)
                
                fig = px.box(quick_laps, x='Team', y='LapTime_s', color='Team', 
                             color_discrete_map=team_colors,
                             title='Team Pace Comparison')
                             
                if sort_by == "Median Lap Time":
                    team_order = quick_laps.groupby('Team')['LapTime_s'].median().sort_values().index
                else:
                    team_order = sorted(quick_laps['Team'].unique())
                
                fig.update_xaxes(categoryorder='array', categoryarray=team_order)
                
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("No quick laps found for pace comparison.")
        except Exception as e:
            st.error(f"Error computing team pace: {e}")

    elif view_type == "Position Changes (Race/Sprint)" and laps is not None:
        st.subheader("Position Changes Lap by Lap")
        if session_type in ["Race", "Sprint"]:
            try:
                race_laps = laps.dropna(subset=['LapNumber', 'Position', 'Driver']).copy()
                if not race_laps.empty:
                    driver_colors = {d: get_driver_color(d, session) for d in race_laps['Driver'].unique()}
                    fig = px.line(race_laps, x='LapNumber', y='Position', color='Driver', 
                                  color_discrete_map=driver_colors,
                                  title='Position Over Time', markers=True)
                    # Reverse Y axis so 1st place is at the top
                    fig.update_yaxes(autorange="reversed")
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.warning("No position data available for this session.")
            except Exception as e:
                st.error(f"Error generating position chart: {e}")
        else:
            st.info("Position changes are most relevant for Race and Sprint sessions.")

    elif view_type == "Lap Time Distribution" and laps is not None:
        st.subheader("Lap Time Distribution per Driver")
        selected_drivers = driver_selector("LapDist", drivers)
        
        if selected_drivers:
            try:
                quick_laps = laps.pick_quicklaps().copy()
                quick_laps['LapTime_s'] = quick_laps['LapTime'].dt.total_seconds()
                
                # Filter strictly by selected_drivers BEFORE drawing the violin plot
                quick_laps = quick_laps[quick_laps['Driver'].isin(selected_drivers)]
                quick_laps = quick_laps.dropna(subset=['LapTime_s', 'Driver'])
                
                if not quick_laps.empty:
                    driver_colors = {d: get_driver_color(d, session) for d in quick_laps['Driver'].unique()}
                    sort_by = st.radio("Sort By", ["Team", "Median Lap Time"], key="sort_lap_dist", horizontal=True)
                    
                    fig = px.violin(quick_laps, x='Driver', y='LapTime_s', color='Driver', box=True, 
                                    color_discrete_map=driver_colors,
                                    title='Lap Time Consistency')
                                    
                    if sort_by == "Median Lap Time":
                        driver_order = quick_laps.groupby('Driver')['LapTime_s'].median().sort_values().index
                    else:
                        # Map drivers to teams to sort by team
                        driver_team_map = quick_laps[['Driver', 'Team']].drop_duplicates().sort_values(by=['Team', 'Driver'])
                        driver_order = driver_team_map['Driver']
                        
                    fig.update_xaxes(categoryorder='array', categoryarray=driver_order)
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.warning("No valid lap data available for the selected drivers.")
            except Exception as e:
                st.error(f"Error generating lap distribution: {e}")
