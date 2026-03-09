"""
app.py
------
Streamlit application for visualising match momentum using the RCI model.

Data sources (must be present in the project root):
    - df_matches.parquet   : match registry (matchId, teamName, home_away, league)
    - momentum_data.h5     : HDF5 file with per-match momentum curves and events

Optional assets:
    - ball_icon.png        : goal marker icon
    - fonts/Teko-*.ttf     : custom font family (falls back to matplotlib default)
"""

import io
import h5py
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.font_manager as font_manager
import streamlit as st
from scipy.ndimage import gaussian_filter1d
from matplotlib.offsetbox import AnnotationBbox, OffsetImage


# ── Fonts (graceful fallback if folder is absent) ─────────────────────────────

def _load_font(path: str, fallback: str = "DejaVu Sans") -> font_manager.FontProperties:
    try:
        return font_manager.FontProperties(fname=path)
    except Exception:
        return font_manager.FontProperties(family=fallback)

font_normal = _load_font("fonts/Teko-Regular.ttf")
font_med    = _load_font("fonts/Teko-Medium.ttf")
font_semi   = _load_font("fonts/Teko-SemiBold.ttf")


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(page_title="Match Momentum | RCI Viewer", layout="wide")


# ── Data loading ──────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def load_matches() -> pd.DataFrame:
    """
    Load the match registry from Parquet.

    Required columns : matchId, teamName, home_away ('h' / 'a')
    Optional columns : league
    """
    return pd.read_parquet("df_matches.parquet")


@st.cache_resource(show_spinner=False)
def load_h5() -> h5py.File:
    """
    Open the HDF5 momentum file (read-only).

    Each group is named after its matchId and contains:
        Datasets  : minutes, diff, goals_minutes, goals_team,
                    shots_minutes, shots_team, shots_on_target
        Attributes: teamA, teamB
    """
    return h5py.File("momentum_data.h5", "r")


@st.cache_resource(show_spinner=False)
def load_ball_icon() -> np.ndarray | None:
    """Load the goal-marker icon. Returns None if ball_icon.png is not found."""
    try:
        return plt.imread("ball_icon.png")
    except FileNotFoundError:
        return None


df_matches = load_matches()
h5file     = load_h5()
ball_img   = load_ball_icon()


# ── UI — sidebar filters ──────────────────────────────────────────────────────

st.title("Match Momentum | RCI Viewer")
st.subheader("Wyscout data 2017/2018")

# League filter (optional column)
if "league" in df_matches.columns:
    leagues    = sorted(df_matches["league"].unique())
    league     = st.selectbox("Select league", leagues)
    df_league  = df_matches[df_matches["league"] == league]
else:
    df_league = df_matches
    st.info("Column 'league' not found in df_matches — showing all matches.")

# Team selector
teams         = sorted(df_league["teamName"].unique())
team_selected = st.selectbox("Select team", teams)

# Build match labels ("Home vs Away") for the selected team
match_ids = sorted(df_league[df_league["teamName"] == team_selected]["matchId"].unique())

options: list[tuple[str, int]] = []
for mid in match_ids:
    rows = df_league[df_league["matchId"] == mid]
    try:
        home = rows.loc[rows["home_away"] == "h", "teamName"].iloc[0]
        away = rows.loc[rows["home_away"] == "a", "teamName"].iloc[0]
    except IndexError:
        unique = rows["teamName"].unique()
        if len(unique) == 2:
            home, away = unique[0], unique[1]
        else:
            continue
    options.append((f"{home} vs {away}", mid))

if not options:
    st.error("No matches found for this team.")
    st.stop()

label_selected    = st.selectbox("Select match", [lab for lab, _ in options])
match_id_selected = dict(options)[label_selected]

# Smoothing slider
sigma_smooth = st.slider("Smoothing level (sigma)", min_value=3, max_value=15, value=6)


# ── H5 data loading ───────────────────────────────────────────────────────────

def load_match_from_h5(h5: h5py.File, match_id: int, sigma: int) -> dict | None:
    """
    Read momentum data for a single match from the HDF5 file.

    The raw diff signal is re-smoothed with the user-selected sigma so the
    slider produces an immediate visual effect without re-reading from disk.

    Home / away assignment is resolved from df_matches rather than relying
    on the teamA / teamB attributes stored in the H5 (which may reflect the
    order in which events were processed, not the actual home/away split).

    Returns a dict ready for plot_momentum(), or None on error.
    """
    mid_str = str(match_id)
    if mid_str not in h5:
        st.error(f"matchId {match_id} not found in momentum_data.h5")
        return None

    g = h5[mid_str]

    minutes   = g["minutes"][:]
    diff_raw  = g["diff"][:]
    diff_smooth = gaussian_filter1d(diff_raw, sigma=sigma)

    goals_minutes = g["goals_minutes"][:]
    goals_team    = [t.decode("utf-8") for t in g["goals_team"][:]]

    shots_minutes   = g["shots_minutes"][:]
    shots_team      = [t.decode("utf-8") for t in g["shots_team"][:]]
    shots_on_target = g["shots_on_target"][:].astype(int)

    # Resolve home / away from df_matches (authoritative source)
    rows = df_matches[df_matches["matchId"] == match_id]
    try:
        home_team = rows.loc[rows["home_away"] == "h", "teamName"].iloc[0]
        away_team = rows.loc[rows["home_away"] == "a", "teamName"].iloc[0]
    except IndexError:
        # Fallback: use H5 attributes if home_away info is missing
        home_team = g.attrs.get("teamA", "Team A")
        away_team = g.attrs.get("teamB", "Team B")

    return {
        "minutes":        minutes,
        "diff_smooth":    diff_smooth,
        "goals_minutes":  goals_minutes,
        "goals_team":     np.array(goals_team),
        "shots_minutes":  shots_minutes,
        "shots_team":     np.array(shots_team),
        "shots_on_target": shots_on_target,
        "teamA":          home_team,   # home
        "teamB":          away_team,   # away
    }


# ── Plot ──────────────────────────────────────────────────────────────────────

def plot_momentum(data: dict) -> plt.Figure:
    """
    Build the match momentum figure.

    Layout:
        - Filled momentum curve (red = home dominance, blue = away dominance)
        - Team name watermarks
        - Goals (ball icon), shots on target (●), shots off target (×)
          all plotted on a single horizontal event line
        - Half-time and full-time vertical markers
    """
    minutes         = data["minutes"]
    diff            = data["diff_smooth"]
    goals_minutes   = data["goals_minutes"]
    goals_team      = data["goals_team"]
    shots_minutes   = data["shots_minutes"]
    shots_team      = data["shots_team"]
    shots_on_target = data["shots_on_target"]
    teamA           = data["teamA"]
    teamB           = data["teamB"]

    COLOR_A = "#D64541"   # home — red
    COLOR_B = "#2E86C1"   # away — blue

    # Safe y_max for event positioning
    y_max = np.nanmax(np.abs(diff)) if len(diff) else 1.0
    if not np.isfinite(y_max) or y_max == 0:
        y_max = 1.0

    y_event_base   = y_max * 0.07   # height of the event marker line
    goal_times_set = set(np.round(goals_minutes, 2))

    fig, ax = plt.subplots(figsize=(12, 7), dpi=150)
    ax.set_facecolor("None")
    fig.set_facecolor("None")

    # ── Momentum curve ────────────────────────────────────────────────────────
    ax.plot(minutes, diff, color="black", linewidth=1.5, alpha=0.75)
    ax.fill_between(minutes, 0, diff, where=(diff > 0), color=COLOR_A, alpha=0.4)
    ax.fill_between(minutes, 0, diff, where=(diff < 0), color=COLOR_B, alpha=0.4)

    # ── Team name watermarks ──────────────────────────────────────────────────
    ax.text(0.4,  0.025, teamA, fontsize=40, weight="bold", alpha=0.10,
            ha="left", va="bottom", color=COLOR_A, fontproperties=font_semi)
    ax.text(0.4, -0.025, teamB, fontsize=40, weight="bold", alpha=0.10,
            ha="left", va="top",    color=COLOR_B, fontproperties=font_semi)

    # ── Goals (ball icon) ─────────────────────────────────────────────────────
    if ball_img is not None and len(goals_minutes) > 0:
        for m, tname in zip(goals_minutes, goals_team):
            direction = 1 if tname == teamA else -1
            ab = AnnotationBbox(
                OffsetImage(ball_img, zoom=0.05),
                (m, y_event_base * direction),
                frameon=False, box_alignment=(0.5, 0.5), zorder=4
            )
            ax.add_artist(ab)

    # ── Shots ─────────────────────────────────────────────────────────────────
    for m, tname, on_t in zip(shots_minutes, shots_team, shots_on_target):
        if np.round(m, 2) in goal_times_set:
            continue   # already shown as a goal icon
        direction = 1 if tname == teamA else -1
        y_event   = y_event_base * direction
        marker    = "o" if on_t == 1 else "x"
        ax.scatter(m, y_event, color="black", marker=marker,
                   s=40, zorder=5, alpha=0.5)

    # ── Reference lines ───────────────────────────────────────────────────────
    ax.axhline(0,  color="gray",  linestyle="--", linewidth=1)
    ax.axvline(45, color="black", linestyle=":",  linewidth=1)
    ax.axvline(90, color="black", linestyle=":",  linewidth=1)

    # ── Axes formatting ───────────────────────────────────────────────────────
    ax.set_xticks(np.arange(0, 91, 10))
    for lbl in ax.get_xticklabels():
        lbl.set_fontproperties(font_normal)
        lbl.set_fontsize(14)
    ax.set_xlabel("Match minutes", fontsize=14, fontproperties=font_med)
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.grid(axis="x", alpha=0.15, linestyle="--")

    # ── Watermark / credit ────────────────────────────────────────────────────
    ax.text(0.99, -0.10, "RCI Model — v1.0 — @AlfoMarino0975",
            transform=ax.transAxes, fontproperties=font_normal,
            fontsize=12, color="gray", ha="right", va="bottom")

    fig.tight_layout()
    return fig


# ── Main action ───────────────────────────────────────────────────────────────

if st.button("Show Match Momentum"):
    data = load_match_from_h5(h5file, match_id_selected, sigma_smooth)

    if data is not None:
        st.markdown(f"### **{data['teamA']} vs {data['teamB']} — Match Momentum (RCI)**")

        fig = plot_momentum(data)
        st.pyplot(fig)

        # Download button
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=300, bbox_inches="tight")
        buf.seek(0)
        st.download_button(
            label="📥 Download chart",
            data=buf,
            file_name=f"match_momentum_{match_id_selected}.png",
            mime="image/png",
        )

        st.markdown("""
### 🗂️ Event legend
| Icon | Meaning |
|------|---------|
| ⚽ | **Goal** |
| ● | **Shot on target** |
| × | **Shot off target** |
""")
