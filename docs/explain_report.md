# Informe explain("executionStats")

En TimeSeries, *docs examinados* son **buckets** internos (cada uno agrupa varios partidos).

| Consulta | Sin índice | Docs examinados | SORT en memoria | Con índice | Índice usado | Claves / Docs examinados | SORT en memoria | ms antes → después |
|---|---|---|---|---|---|---|---|---|
| Máximos goleadores (índice compuesto goals_assists) | COLLSCAN (—) | 47,621 | sí | IXSCAN | `goals_assists` | 20 / 20 | no | 49 → 0 |
| Jugadores más valiosos (índice market_value) | COLLSCAN (—) | 47,621 | sí | IXSCAN | `market_value` | 133 / 133 | no | 48 → 1 |
| Filtros del dashboard: temporada + liga + posición + edad (ESR) | IXSCAN (season_club) | 3,440 | no | IXSCAN | `season_league_position_age` | 13 / 13 | no | 13 → 1 |
| Pipeline de ranking ordenado por probabilidad ML ($match + $sort -> índice) | IXSCAN (season_club) | 3,440 | sí | IXSCAN | `season_ml_probability` | 3,440 / 3,440 | no | 76 → 73 |
| TimeSeries: partidos de un jugador por fecha | COLLSCAN (—) | 78,836 | no | IXSCAN | `player_date` | 37 / 37 | no | 72 → 2 |
