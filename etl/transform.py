"""Transforma los CSV crudos del Data Lake de Transfermarkt en documentos para MongoDB.

Fuentes (data/raw):
  Data Lake salimt/football-datasets  -> player_profiles, player_market_value,
                                         player_national_performances, team_competitions_seasons
  Transfermarkt dcaribou (mismos IDs) -> appearances (por partido), games, competitions,
                                         clubs, players, player_valuations

Toda temporada se define como ventana 1-ago -> 31-jul (periodo de evaluación del Balón de Oro).
"""
import re
import unicodedata

import numpy as np
import pandas as pd

from config import FIRST_SEASON, MIN_MINUTES, RAW_DIR, season_label

TOP5 = {"GB1", "ES1", "IT1", "L1", "FR1"}
NATIONAL_COMPS = {"FIWC", "EURO", "COPA", "AFCN", "AFAC"}

# Peso de cada título en la "puntuación de títulos".
TITLE_WEIGHTS = {
    "CL": 5.0, "FIWC": 5.0, "EURO": 4.0, "COPA": 4.0, "AFCN": 2.5, "AFAC": 2.0,
    "EL": 2.0, "KLUB": 1.5, "UCOL": 1.0, "USC": 0.5, "CGB": 0.5,
}
COMP_NAMES = {
    "GB1": "Premier League", "ES1": "LaLiga", "IT1": "Serie A", "L1": "Bundesliga", "FR1": "Ligue 1",
    "PO1": "Liga Portugal", "NL1": "Eredivisie", "TR1": "Süper Lig", "BE1": "Jupiler Pro League",
    "SC1": "Scottish Premiership", "CL": "Champions League", "EL": "Europa League",
    "UCOL": "Conference League", "USC": "Supercopa de Europa", "KLUB": "Mundial de Clubes",
    "FIWC": "Mundial", "EURO": "Eurocopa", "COPA": "Copa América", "AFCN": "Copa África",
    "AFAC": "Copa Asiática", "CDR": "Copa del Rey", "SUC": "Supercopa de España", "FAC": "FA Cup",
    "CGB": "EFL Cup", "GBCS": "Community Shield", "DFB": "DFB-Pokal", "DFL": "Supercopa de Alemania",
    "CIT": "Coppa Italia", "SCI": "Supercoppa Italiana", "FRCH": "Trophée des Champions",
    "POCP": "Taça da Liga", "POSU": "Supertaça", "NLP": "KNVB Beker", "NLSC": "Johan Cruijff Schaal",
}
LEAGUE_WEIGHT_TOP5, LEAGUE_WEIGHT_OTHER = 3.0, 1.5
DOMESTIC_CUP_WEIGHT, SUPERCUP_WEIGHT = 1.0, 0.5

# Podios reales del Balón de Oro (año de gala -> temporada que evalúa = año - 1).
# Hasta 2021 el premio era por año natural; la ventana ago-jul es una aproximación.
BALLON_DOR_PODIUMS = {
    2013: ["Cristiano Ronaldo", "Lionel Messi", "Franck Ribéry"],
    2014: ["Cristiano Ronaldo", "Lionel Messi", "Manuel Neuer"],
    2015: ["Lionel Messi", "Cristiano Ronaldo", "Neymar"],
    2016: ["Cristiano Ronaldo", "Lionel Messi", "Antoine Griezmann"],
    2017: ["Cristiano Ronaldo", "Lionel Messi", "Neymar"],
    2018: ["Luka Modrić", "Cristiano Ronaldo", "Antoine Griezmann"],
    2019: ["Lionel Messi", "Virgil van Dijk", "Cristiano Ronaldo"],
    2021: ["Lionel Messi", "Robert Lewandowski", "Jorginho"],
    2022: ["Karim Benzema", "Sadio Mané", "Kevin De Bruyne"],
    2023: ["Lionel Messi", "Erling Haaland", "Kylian Mbappé"],
    2024: ["Rodri", "Vinicius Junior", "Jude Bellingham"],
    2025: ["Ousmane Dembélé", "Lamine Yamal", "Vitinha"],
}


def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def season_of(dates: pd.Series) -> pd.Series:
    return np.where(dates.dt.month >= 8, dates.dt.year, dates.dt.year - 1)


# --------------------------------------------------------------------------- carga
def load_raw():
    app = pd.read_csv(
        RAW_DIR / "appearances.csv.gz",
        usecols=["game_id", "player_id", "player_club_id", "date", "competition_id",
                 "yellow_cards", "red_cards", "goals", "assists", "minutes_played"],
        parse_dates=["date"],
    )
    app["season"] = season_of(app["date"])
    app = app[app.season >= FIRST_SEASON]

    games = pd.read_csv(RAW_DIR / "games.csv.gz", parse_dates=["date"])
    games["season_window"] = season_of(games["date"])
    comps = pd.read_csv(RAW_DIR / "competitions.csv.gz")
    clubs = pd.read_csv(RAW_DIR / "clubs.csv.gz", usecols=["club_id", "name"])
    return app, games, comps, clubs


def load_profiles():
    dc = pd.read_csv(RAW_DIR / "players.csv.gz", usecols=[
        "player_id", "name", "date_of_birth", "position", "sub_position", "foot",
        "height_in_cm", "country_of_citizenship", "image_url"], parse_dates=["date_of_birth"])
    lake = pd.read_csv(RAW_DIR / "player_profiles.csv", low_memory=False, usecols=[
        "player_id", "player_name", "player_image_url", "date_of_birth", "citizenship",
        "position", "main_position", "foot", "height", "place_of_birth"])
    lake = lake.rename(columns={"position": "lake_position", "date_of_birth": "lake_dob",
                                "foot": "lake_foot"})
    lake["lake_name"] = lake.player_name.str.replace(r"\s*\(\d+\)$", "", regex=True)
    # "France  Mali" -> ["France", "Mali"] (el Data Lake separa con doble espacio)
    lake["citizenships"] = lake.citizenship.fillna("").str.split(r"\s{2,}").apply(
        lambda xs: [x.strip() for x in xs if x.strip()])
    prof = dc.merge(lake, on="player_id", how="outer")

    prof["name"] = prof["name"].fillna(prof["lake_name"])
    prof["date_of_birth"] = prof["date_of_birth"].fillna(pd.to_datetime(prof["lake_dob"], errors="coerce"))
    prof["position"] = prof["position"].fillna(prof["main_position"])
    prof["sub_position"] = prof["sub_position"].fillna(
        prof["lake_position"].str.split(" - ").str[-1])
    prof["foot"] = prof["foot"].fillna(prof["lake_foot"]).replace("N/A", np.nan)
    # Hay erratas en origen (p. ej. 19 cm): fuera de rango -> desconocido.
    prof["height_cm"] = prof["height_in_cm"].fillna(prof["height"]).where(lambda h: h.between(140, 220))
    prof["image_url"] = prof["image_url"].fillna(prof["player_image_url"])
    prof["citizenships"] = [
        c if isinstance(c, list) and c else ([n] if isinstance(n, str) else [])
        for c, n in zip(prof["citizenships"], prof["country_of_citizenship"])
    ]
    prof["nationality"] = prof["citizenships"].str[0]

    nat = pd.read_csv(RAW_DIR / "player_national_performances.csv")
    # La selección absoluta es la que acumula más partidos.
    nat = nat.sort_values("matches", ascending=False).drop_duplicates("player_id")
    prof = prof.merge(nat[["player_id", "matches", "goals"]].rename(
        columns={"matches": "national_caps", "goals": "national_goals"}), on="player_id", how="left")
    prof[["national_caps", "national_goals"]] = prof[["national_caps", "national_goals"]].fillna(0)
    return prof[["player_id", "name", "date_of_birth", "position", "sub_position", "foot",
                 "height_cm", "image_url", "citizenships", "nationality", "place_of_birth",
                 "national_caps", "national_goals"]].drop_duplicates("player_id")


def load_market_values():
    lake = pd.read_csv(RAW_DIR / "player_market_value.csv").rename(
        columns={"date_unix": "date", "value": "value_eur"})
    dc = pd.read_csv(RAW_DIR / "player_valuations.csv.gz", usecols=[
        "player_id", "date", "market_value_in_eur"]).rename(columns={"market_value_in_eur": "value_eur"})
    mv = pd.concat([lake, dc], ignore_index=True)
    mv["date"] = pd.to_datetime(mv["date"], errors="coerce")
    mv = mv.dropna().query("value_eur > 0")
    return mv.drop_duplicates(["player_id", "date"]).sort_values("date").reset_index(drop=True)


# --------------------------------------------------------------------------- títulos
def league_champions(games: pd.DataFrame) -> pd.DataFrame:
    """Campeón de cada liga y temporada, calculado desde los resultados de los partidos."""
    lg = games[games.competition_type == "domestic_league"].dropna(subset=["home_club_goals"])
    rows = []
    for side, other in (("home", "away"), ("away", "home")):
        gf, ga = lg[f"{side}_club_goals"], lg[f"{other}_club_goals"]
        rows.append(pd.DataFrame({
            "competition_id": lg.competition_id, "season": lg.season,
            "club_id": lg[f"{side}_club_id"],
            "pts": np.select([gf > ga, gf == ga], [3, 1], 0), "gd": gf - ga, "gf": gf,
        }))
    table = pd.concat(rows).groupby(["competition_id", "season", "club_id"], as_index=False).sum()
    table = table.sort_values(["pts", "gd", "gf"], ascending=False)
    return table.drop_duplicates(["competition_id", "season"])[["competition_id", "season", "club_id"]]


def final_winners(games: pd.DataFrame) -> pd.DataFrame:
    """Ganador de cada final a partido único. Los goles ya incluyen la tanda de penaltis."""
    f = games[games["round"].astype(str).str.lower().eq("final")].dropna(subset=["home_club_goals"])
    f = f[f.home_club_goals != f.away_club_goals]
    home_won = f.home_club_goals > f.away_club_goals
    return pd.DataFrame({
        "competition_id": f.competition_id, "season": f.season_window,
        "club_id": np.where(home_won, f.home_club_id, f.away_club_id),
        "winner_name": np.where(home_won, f.home_club_name, f.away_club_name),
    })


def comp_display_names(comps: pd.DataFrame) -> pd.Series:
    generic = comps.set_index("competition_id")["name"].str.replace("-", " ").str.title()
    return pd.Series(COMP_NAMES).combine_first(generic)


def title_weight(comp_id, comp_type, sub_type):
    if comp_id in TITLE_WEIGHTS:
        return TITLE_WEIGHTS[comp_id]
    if comp_type == "domestic_league":
        return LEAGUE_WEIGHT_TOP5 if comp_id in TOP5 else LEAGUE_WEIGHT_OTHER
    if comp_type == "domestic_cup":
        return DOMESTIC_CUP_WEIGHT
    if sub_type == "domestic_super_cup":
        return SUPERCUP_WEIGHT
    return 0.5


def player_titles(app, games, comps, profiles):
    """Lista de títulos (competition_id, season) ganados por cada jugador."""
    champs = league_champions(games)
    finals = final_winners(games)
    club_titles = pd.concat([champs, finals[~finals.competition_id.isin(NATIONAL_COMPS)]
                             [["competition_id", "season", "club_id"]]])
    # Club: el jugador cuenta como campeón si jugó esa competición con ese club esa temporada.
    played = app[["player_id", "player_club_id", "competition_id", "season"]].drop_duplicates()
    won = played.merge(club_titles, left_on=["competition_id", "season", "player_club_id"],
                       right_on=["competition_id", "season", "club_id"])[["player_id", "competition_id", "season"]]

    # Selecciones: por apariciones si existen; si no, por nacionalidad + ≥10 internacionalidades.
    nat_finals = finals[finals.competition_id.isin(NATIONAL_COMPS)]
    by_app = played.merge(nat_finals, left_on=["competition_id", "season", "player_club_id"],
                          right_on=["competition_id", "season", "club_id"])
    covered = set(zip(by_app.competition_id, by_app.season))
    fallback = nat_finals[[(c, s) not in covered for c, s in zip(nat_finals.competition_id, nat_finals.season)]]
    internationals = profiles[profiles.national_caps >= 10][["player_id", "nationality"]]
    active = app[["player_id", "season"]].drop_duplicates()
    by_nat = fallback.merge(internationals, left_on="winner_name", right_on="nationality") \
                     .merge(active, on=["player_id", "season"])
    won = pd.concat([won, by_app[["player_id", "competition_id", "season"]],
                     by_nat[["player_id", "competition_id", "season"]]]).drop_duplicates()

    meta = comps.set_index("competition_id")
    # Algunas finales (p. ej. UKRS) son de competiciones ausentes del catálogo: usar su código.
    won["name"] = won.competition_id.map(comp_display_names(comps)).fillna(won.competition_id)
    won["weight"] = [title_weight(c, meta["type"].get(c), meta["sub_type"].get(c)) for c in won.competition_id]
    return won


# --------------------------------------------------------------------------- agregados
def team_results(app, games):
    """Resultado (W/D/L) del equipo del jugador en cada partido."""
    g = games.set_index("game_id")[["home_club_id", "home_club_goals", "away_club_goals"]]
    r = app.join(g, on="game_id")
    is_home = r.player_club_id == r.home_club_id
    gf = np.where(is_home, r.home_club_goals, r.away_club_goals)
    ga = np.where(is_home, r.away_club_goals, r.home_club_goals)
    return pd.Series(np.select([gf > ga, gf == ga], ["W", "D"], "L"), index=app.index).where(~np.isnan(gf))


def build_player_seasons(app, games, comps, clubs, profiles, mv, titles):
    a = app.copy()
    a["result"] = team_results(app, games)
    a["is_top5"] = a.competition_id.isin(TOP5)
    a["is_cl"] = a.competition_id.eq("CL")
    a["is_nat"] = a.competition_id.isin(NATIONAL_COMPS)
    a["ga"] = a.goals + a.assists
    for col in ("goals", "assists", "ga", "minutes_played"):
        a[f"cl_{col}"] = a[col].where(a.is_cl, 0)
        a[f"nat_{col}"] = a[col].where(a.is_nat, 0)

    agg = a.groupby(["player_id", "season"]).agg(
        apps=("game_id", "size"), minutes=("minutes_played", "sum"),
        goals=("goals", "sum"), assists=("assists", "sum"),
        yellow=("yellow_cards", "sum"), red=("red_cards", "sum"),
        top5_apps=("is_top5", "sum"),
        cl_apps=("is_cl", "sum"), cl_goals=("cl_goals", "sum"), cl_assists=("cl_assists", "sum"),
        nat_apps=("is_nat", "sum"), nat_goals=("nat_goals", "sum"), nat_assists=("nat_assists", "sum"),
        wins=("result", lambda s: (s == "W").sum()), draws=("result", lambda s: (s == "D").sum()),
        decided=("result", "count"),
    ).reset_index()
    agg = agg[agg.minutes >= MIN_MINUTES]

    # Club y liga principales = los de más minutos (sin contar selección).
    league_ids = set(comps[comps.type == "domestic_league"].competition_id)
    club_min = a[~a.is_nat].groupby(["player_id", "season", "player_club_id"]).minutes_played.sum() \
                           .reset_index().sort_values("minutes_played", ascending=False) \
                           .drop_duplicates(["player_id", "season"])
    lg_min = a[a.competition_id.isin(league_ids)].groupby(["player_id", "season", "competition_id"]) \
              .minutes_played.sum().reset_index().sort_values("minutes_played", ascending=False) \
              .drop_duplicates(["player_id", "season"])
    agg = agg.merge(club_min[["player_id", "season", "player_club_id"]], on=["player_id", "season"], how="left") \
             .merge(lg_min[["player_id", "season", "competition_id"]], on=["player_id", "season"], how="inner")
    agg = agg.rename(columns={"player_club_id": "club_id", "competition_id": "league_id"})
    club_names = clubs.set_index("club_id")["name"]
    agg["club_name"] = agg.club_id.map(club_names).fillna("Desconocido")
    cmeta = comps.set_index("competition_id")
    agg["league_name"] = agg.league_id.map(comp_display_names(comps))
    agg["league_country"] = agg.league_id.map(cmeta["country_name"])

    # Títulos
    t = titles.groupby(["player_id", "season"]).apply(
        lambda d: pd.Series({"title_points": d.weight.sum(),
                             "titles": d[["competition_id", "name", "weight"]].to_dict("records")}),
        include_groups=False).reset_index()
    agg = agg.merge(t, on=["player_id", "season"], how="left")
    agg["title_points"] = agg.title_points.fillna(0.0)
    agg["titles"] = agg.titles.apply(lambda x: x if isinstance(x, list) else [])

    # Valor de mercado al final (31-jul) y al inicio (1-ago) de la temporada.
    agg["end_date"] = pd.to_datetime((agg.season + 1).astype(str) + "-07-31")
    agg["start_date"] = pd.to_datetime(agg.season.astype(str) + "-08-01")
    mv_s = mv.rename(columns={"date": "mv_date"})
    for col, key in (("market_value_eur", "end_date"), ("market_value_start_eur", "start_date")):
        agg = agg.sort_values(key)
        agg = pd.merge_asof(agg, mv_s.rename(columns={"value_eur": col}).sort_values("mv_date"),
                            left_on=key, right_on="mv_date", by="player_id", direction="backward") \
                .drop(columns="mv_date")
    agg[["market_value_eur", "market_value_start_eur"]] = agg[["market_value_eur", "market_value_start_eur"]].fillna(0)

    agg = agg.merge(profiles, on="player_id", how="inner")
    agg["age"] = ((agg.end_date - agg.date_of_birth).dt.days / 365.25).round(1)
    agg = agg[agg.age.between(15, 50) & agg.position.isin(["Attack", "Midfield", "Defender", "Goalkeeper"])]
    return agg.sort_values(["season", "player_id"]).reset_index(drop=True)


def resolve_ballon_dor(ps: pd.DataFrame) -> pd.DataFrame:
    """Asigna player_id a los podios históricos buscando por nombre normalizado en la temporada."""
    ps = ps.assign(norm=ps.name.map(normalize_name))
    rows, missing = [], []
    for year, names in BALLON_DOR_PODIUMS.items():
        cands = ps[ps.season == year - 1]
        for rank, name in enumerate(names, 1):
            hit = cands[cands.norm == normalize_name(name)].sort_values("market_value_eur", ascending=False)
            if hit.empty:
                missing.append(f"{year} #{rank} {name}")
                continue
            rows.append({"award_year": year, "season": year - 1, "rank": rank,
                         "player_id": int(hit.player_id.iloc[0]), "name": name})
    return pd.DataFrame(rows), missing


def build_all():
    app, games, comps, clubs = load_raw()
    profiles = load_profiles()
    mv = load_market_values()
    titles = player_titles(app, games, comps, profiles)
    ps = build_player_seasons(app, games, comps, clubs, profiles, mv, titles)
    bdo, missing = resolve_ballon_dor(ps)

    candidates = ps[ps.season.isin([2024, 2025])].player_id.unique()
    matches = app[app.player_id.isin(candidates) & app.season.isin([2024, 2025])].copy()
    matches["result"] = team_results(matches, games)
    matches = matches.merge(clubs.rename(columns={"club_id": "player_club_id", "name": "club_name"}),
                            on="player_club_id", how="left")
    mv_ts = mv[mv.player_id.isin(candidates) & (mv.date >= "2015-01-01")]
    return {"player_seasons": ps, "matches": matches, "market_values": mv_ts,
            "ballon_dor": bdo, "bdo_missing": missing, "competitions": comps,
            "league_champions": league_champions(games)}
