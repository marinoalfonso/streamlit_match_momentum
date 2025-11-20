import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import h5py
import matplotlib.font_manager as font_manager
from scipy.ndimage import gaussian_filter1d
from matplotlib.offsetbox import OffsetImage, AnnotationBbox


font_path_regular = '/Users/alfonsomarino/Desktop/Teko/static/Teko-Regular.ttf'
font_normal = font_manager.FontProperties(fname=font_path_regular)

font_path_med = "/Users/alfonsomarino/Desktop/Teko/static/Teko-Medium.ttf"
font_med = font_manager.FontProperties(fname = font_path_med)

font_path_semi = "/Users/alfonsomarino/Desktop/Teko/static/Teko-SemiBold.ttf"
font_semi = font_manager.FontProperties(fname = font_path_semi)

# =====================================
# 1. CONFIGURAZIONE BASE
# =====================================
st.set_page_config(page_title="Match Momentum | RCI viewer", layout="wide")

# =====================================
# 2. FUNZIONI DI CARICAMENTO
# =====================================

@st.cache_data(show_spinner=False)
def load_matches():
    """
    Carica df_matches salvato come parquet.
    Deve contenere almeno: matchId, teamName, home_away ('h'/'a').
    Opzionale: league.
    """
    df = pd.read_parquet("df_matches.parquet")
    return df

@st.cache_resource(show_spinner=False)
def load_h5():
    """
    Apre il file HDF5 con i dati del momentum.
    Ogni gruppo ha nome = matchId e contiene:
    - minutes
    - diff
    - goals_minutes
    - goals_team
    - shots_minutes
    - shots_team
    - shots_on_target
    Attributi:
    - teamA
    - teamB
    """
    return h5py.File("momentum_data.h5", "r")

@st.cache_resource(show_spinner=False)
def load_ball_icon():
    """
    Carica l'icona del pallone.
    Assumo che il file si chiami 'ball_icon.png'
    nella stessa cartella di app.py.
    """
    try:
        img = plt.imread("ball_icon.png")
    except FileNotFoundError:
        img = None
    return img


df_matches = load_matches()
h5file = load_h5()
ball_img = load_ball_icon()

# =====================================
# 3. UI – SELEZIONE LEGA, SQUADRA, PARTITA
# =====================================

st.title("Match Momentum | RCI Viewer")

# Filtro per campionato (se disponibile)
if "league" in df_matches.columns:
    leagues = sorted(df_matches["league"].unique())
    league = st.selectbox("Seleziona campionato", leagues)
    df_league = df_matches[df_matches["league"] == league]
else:
    df_league = df_matches
    st.info("Colonna 'league' non trovata in df_matches: uso tutte le partite.")

# Selezione squadra
teams = sorted(df_league["teamName"].unique())
team_selected = st.selectbox("Seleziona squadra", teams)

# Tutti i matchId in cui la squadra è coinvolta
match_ids_team = sorted(df_league[df_league["teamName"] == team_selected]["matchId"].unique())

# Costruisco le etichette tipo "Home vs Away" usando home_away
options = []
for mid in match_ids_team:
    rows = df_league[df_league["matchId"] == mid]

    try:
        home_team = rows.loc[rows["home_away"] == "h", "teamName"].iloc[0]
        away_team = rows.loc[rows["home_away"] == "a", "teamName"].iloc[0]
    except IndexError:
        # Fallback nel caso manchino info home/away
        unique_teams = rows["teamName"].unique()
        if len(unique_teams) == 2:
            home_team, away_team = unique_teams[0], unique_teams[1]
        else:
            continue

    label = f"{home_team} vs {away_team}"
    options.append((label, mid))

if not options:
    st.error("Nessuna partita trovata per questa squadra.")
    st.stop()

labels = [lab for lab, _ in options]
label_selected = st.selectbox("Seleziona partita", labels)
match_id_selected = dict(options)[label_selected]

# Slider smoothing
sigma_smooth = st.slider("Livello di smoothing (sigma)", 3, 15, 6)

# =====================================
# 4. FUNZIONE PER LEGGERE I DATI DAL FILE H5
# =====================================

def load_match_from_h5(h5file, match_id, sigma):
    """
    Legge i dati del match dal file HDF5 e calcola
    la curva di momentum smussata con il sigma selezionato.
    """
    mid_str = str(match_id)
    if mid_str not in h5file:
        st.error(f"MatchId {match_id} non trovato in momentum_data.h5")
        return None

    g = h5file[mid_str]

    minutes = g["minutes"][:]
    diff_raw = g["diff"][:]            # diff salvata (la ri-smussiamo con sigma selezionato)

    # smoothing con sigma scelto dall'utente
    diff_smooth = gaussian_filter1d(diff_raw, sigma=sigma)

    # Gol
    goals_minutes = g["goals_minutes"][:]
    goals_team_bytes = g["goals_team"][:]
    goals_team = [t.decode("utf-8") for t in goals_team_bytes]

    # Tiri
    shots_minutes = g["shots_minutes"][:]
    shots_team_bytes = g["shots_team"][:]
    shots_team = [t.decode("utf-8") for t in shots_team_bytes]
    shots_on_target = g["shots_on_target"][:]

    # Nom i squadre (come definite in df_success al momento del salvataggio)
    # ==========================
    #  RIASSEGNO teamA/teamB come HOME / AWAY usando df_matches
    # ==========================

    rows = df_matches[df_matches["matchId"] == match_id]

    try:
        home_team = rows.loc[rows["home_away"] == "h", "teamName"].iloc[0]
        away_team = rows.loc[rows["home_away"] == "a", "teamName"].iloc[0]
    except Exception:
        # Fallback: se manca home_away uso la logica originale
        home_team = teamA
        away_team = teamB

    # Sovrascrivo i valori letti dall'H5
    data = {
        "minutes": minutes,
        "diff_smooth": diff_smooth,
        "goals_minutes": goals_minutes,
        "goals_team": np.array(goals_team),
        "shots_minutes": shots_minutes,
        "shots_team": np.array(shots_team),
        "shots_on_target": shots_on_target.astype(int),

        # QUI li riscriviamo come HOME e AWAY
        "teamA": home_team,
        "teamB": away_team,
    }

    return data

# =====================================
# 5. PLOT DEL MATCH MOMENTUM
# =====================================

def plot_momentum(data):
    minutes = data["minutes"]
    diff = data["diff_smooth"]

    goals_minutes = data["goals_minutes"]
    goals_team = data["goals_team"]

    shots_minutes = data["shots_minutes"]
    shots_team = data["shots_team"]
    shots_on_target = data["shots_on_target"]

    teamA = data["teamA"]
    teamB = data["teamB"]

    # Colori squadre
    colorA = "#D64541"
    colorB = "#2E86C1"

    # Magnitudine massima per posizionare scritte e simboli
    if len(diff) == 0:
        y_max = 1.0
    else:
        y_max = np.nanmax(np.abs(diff))
        if not np.isfinite(y_max) or y_max == 0:
            y_max = 1.0

    y_team_label = y_max * 0.8
    y_goal_base  = y_max * 0.6
    y_shot_base  = y_max * 0.4

    fig, ax = plt.subplots(figsize=(12, 7), dpi = 150)
    
    ax.set_facecolor("#FAFAFA")
    fig.set_facecolor("#FAFAFA")

    # Curva momentum
    ax.plot(
        minutes,
        diff,
        color="black",
        linewidth=1.5,
        alpha = 0.75
    )

    # Aree di dominio
    ax.fill_between(
        minutes,
        0,
        diff,
        where=(diff > 0),
        color=colorA,
        alpha=0.4,
    )
    ax.fill_between(
        minutes,
        0,
        diff,
        where=(diff < 0),
        color=colorB,
        alpha=0.4,
    )

    # Nomi squadre dentro il grafico, ancorati alla linea 0
    #x_text = minutes.min() + (minutes.max() - minutes.min()) * 0.02

    ax.text(
        0.4,
        0.025,
        teamA,
        fontsize=40,
        weight="bold",
        alpha=0.1,
        ha="left",
        va="bottom",
        color=colorA,
        fontproperties = font_semi
    )
    ax.text(
        0.4,
        -0.025,
        teamB,
        fontsize=40,
        weight="bold",
        alpha=0.1,
        ha="left",
        va="top",
        color=colorB,
        fontproperties = font_semi
    )

    # ================================
    #  LINEA UNICA PER TUTTI GLI EVENTI
    # ================================
    y_event_base = 0.07  # stessa altezza per gol + tiri

    # Set di tempi goal (per evitare sovrapposizioni)
    goal_times_set = set(np.round(goals_minutes, 2))

    # ------------------------
    #     GOL (icona pallone)
    # ------------------------
    if ball_img is not None and len(goals_minutes) > 0:
        for m, tname in zip(goals_minutes, goals_team):
            direction = 1 if tname == teamA else -1
            y_event = y_event_base * direction  # ----> LINEA UNICA

            image = OffsetImage(ball_img, zoom=0.05)
            ab = AnnotationBbox(
                image,
                (m, y_event),
                frameon=False,
                box_alignment=(0.5, 0.5), zorder = 4
            )
            ax.add_artist(ab)

    # ------------------------
    #     TIRI (pallino / X)
    # ------------------------
    for m, tname, on_t in zip(shots_minutes, shots_team, shots_on_target):

        # Se coincide con un gol → non disegno
        if np.round(m, 2) in goal_times_set:
            continue

        direction = 1 if tname == teamA else -1
        y_event = y_event_base * direction  # ----> LINEA UNICA

        if on_t == 1:
            # Tiro in porta → pallino
            ax.scatter(
                m, y_event,
                color="black",
                s=40,
                zorder=5, alpha=0.5
            )
        else:
            # Tiro fuori → X
            ax.scatter(
                m, y_event,
                color="black",
                marker="x",
                s=40,
                zorder=5, alpha=0.5
            )

    # Linee di riferimento
    ax.axhline(0, color="gray", linestyle="--", linewidth=1)
    ax.axvline(45, color="black", linestyle=":", linewidth=1)
    ax.axvline(90, color="black", linestyle=":", linewidth=1)

    # Limiti assi
    #ax.set_xlim(0, 90)
    ax.set_xticks(np.arange(0, 91, 10))
    for label in ax.get_xticklabels():
            label.set_fontproperties(font_normal)
            label.set_fontsize(14)

    ax.set_xlabel("Minuti di gioco", fontsize=14, fontproperties = font_med)
    #ax.set_ylabel("Controllo della partita (RCI)", fontsize=12)

    # Niente legenda
    # Nessuna etichetta sull'asse Y
    ax.set_yticks([])

    # Rimozione dei bordi (spines)
    for spine in ax.spines.values():
        spine.set_visible(False)

    # Griglia leggera solo verticale
    ax.grid(axis="x", alpha=0.15, linestyle="--")
    
    ax.text(0.99, -0.1, "RCI Model — v1.0 — @AlfoMarino0975",
            transform=ax.transAxes, fontproperties = font_normal,
            fontsize=12, color="gray", ha="right", va="bottom")

    st.markdown(f"### **{teamA} vs {teamB} — Match Momentum (RCI)**")

    fig.tight_layout()
    return fig

# =====================================
# 6. BUTTON – MOSTRA GRAFICO
# =====================================



if st.button("Mostra Match Momentum"):
    data = load_match_from_h5(h5file, match_id_selected, sigma_smooth)

    if data is not None:
        fig = plot_momentum(data)
        st.pyplot(fig)
        
        import io

        # Converti il grafico in buffer PNG
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=300, bbox_inches="tight")
        buf.seek(0)

        # Bottone di download
        st.download_button(
            label="📥 Scarica grafico",
            data=buf,
            file_name=f"match_momentum.png",
            mime="image/png"
        )
        
        st.markdown("""
                ### 🗂️ Legenda eventi
                | Icona | Significato |
                |-------|-------------|
                | ⚽ | **Gol** |
                | ● | **Tiro in porta** |
                | × | **Tiro fuori** |
                """)