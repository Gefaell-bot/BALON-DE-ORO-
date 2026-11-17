# Balón de Oro Analytics

Aplicación web sobre **MongoDB** que analiza qué jugador opta a ganar el Balón de Oro. Combina
el *Global Football Data Lake* de Transfermarkt con datos partido a partido, calcula una
**Puntuación Balón de Oro** con un pipeline de agregación y estima la **probabilidad de ganar**
con un modelo de scikit-learn validado contra las galas reales de 2013 a 2025.

```
data/raw/*.csv ──► etl/transform.py ──► etl/seed.py ──► MongoDB (balon_de_oro)
                                                          │  player_seasons  ($jsonSchema)
                                                          │  match_performances (TimeSeries)
                                                          │  market_values      (TimeSeries)
                                                          │  ballon_dor_history, competitions, model_runs
                    ml/train.py  ◄── lee player_seasons ──┤
                    escribe ml.* ─────────────────────────┤
                                                          ▼
                    backend/main.py (FastAPI) ◄──► frontend/ (HTML + CSS + JS + Chart.js)
```

| Entregable | Fichero |
|---|---|
| Ingesta, validación JSON Schema y TimeSeries | `etl/transform.py`, `etl/schema.py`, `etl/seed.py` |
| Verificación de integridad | `scripts/verify_data.py` |
| Verificación de índices con `.explain()` | `scripts/explain_indexes.py` → `docs/explain_report.md` |
| Base de datos en un único fichero JSON/CSV | `scripts/export_dataset.py` → `data/export/` |
| Backend con pipelines y lógica analítica | `backend/`, `ml/` |
| Frontend | `frontend/` |
| Tests de la API | `tests/test_api.py` |

### Estructura del proyecto

```
PROYECTO FINAL/
├── config.py              Configuración común (MONGO_URI, MONGO_DB, rutas)
├── requirements.txt
├── README.md
├── netlify.toml           Despliegue del frontend en Netlify (proxy de /api a Render)
├── render.yaml            Despliegue del backend en Render
├── backend/               API FastAPI
│   ├── main.py            Endpoints y servido del frontend
│   ├── models.py          Modelos Pydantic de entrada
│   └── pipelines.py       Pipelines de agregación de MongoDB
├── etl/                   Ingesta de datos
│   ├── transform.py       Limpieza y unión de los CSV
│   ├── schema.py          Validación $jsonSchema e índices
│   └── seed.py            Carga en MongoDB
├── ml/                    Modelo predictivo
│   ├── features.py        Variables del modelo
│   ├── train.py           Entrenamiento y validación por temporada
│   ├── predict.py         Inferencia
│   └── model.joblib       Modelo entrenado
├── frontend/              Dashboard (index.html, styles.css, app.js, favicon.svg)
├── scripts/               Utilidades (verify_data, explain_indexes, export_dataset)
├── tests/                 Tests de la API
├── data/
│   ├── raw/               CSV originales de Transfermarkt
│   └── export/            Base de datos exportada (JSON y CSV)
└── docs/
    ├── explain_report.md  Informe de índices
    └── explain/           Planes explain() antes/después de cada índice
```

---

## 1. Puesta en marcha

Requisitos: Python 3.11+ y MongoDB 7+ en `mongodb://localhost:27017` (probado con 9.0.2).

```powershell
cd "PROYECTO FINAL"
python -m pip install -r requirements.txt

python -m etl.seed                 # ~1 min: transforma los CSV y carga MongoDB
python -m ml.train                 # entrena el modelo y escribe ml.* en cada jugador
python -m scripts.verify_data      # 12 comprobaciones de integridad
python -m scripts.explain_indexes  # COLLSCAN -> IXSCAN, genera docs/explain_report.md
python -m tests.test_api           # 19 tests de la API (sobre una BD temporal)

python -m uvicorn backend.main:app --port 8000
```

- Dashboard: <http://localhost:8000>
- Documentación interactiva (Swagger): <http://localhost:8000/docs>

Variables opcionales: `MONGO_URI` (por defecto `mongodb://localhost:27017`) y `MONGO_DB` (por defecto `balon_de_oro`).

### Cargar la base de datos desde un único fichero

Si solo quieres los datos, sin ejecutar el ETL, usa `data/export/` (se regenera con `python -m scripts.export_dataset`):

| Fichero | Formato | Contenido |
|---|---|---|
| `balon_de_oro.json` | Array en Extended JSON (las fechas van como `{"$date": ...}`) | 47.621 documentos jugador-temporada (2012-13 a 2025-26), con perfil, rendimiento, Champions, selección, títulos, valor, podio real y predicción |
| `balon_de_oro.csv` | CSV UTF-8 con los campos anidados aplanados en notación punto (`performance.goals`) | Lo mismo en 53 columnas |

- **Compass:** crea la base `balon_de_oro` y la colección `player_seasons`, pulsa *Add data → Import JSON or CSV file* y elige `balon_de_oro.json`.
- **Línea de comandos:** `mongoimport --uri mongodb://localhost:27017/balon_de_oro --collection player_seasons --jsonArray --file data/export/balon_de_oro.json`

Comprobado: el JSON se reimporta entero (47.621 documentos), conserva las fechas como `Date` y pasa el `$jsonSchema`. Para tener también los validadores, los índices y las TimeSeries, ejecuta `python -m etl.seed`.

### Despliegue en Internet (Atlas + Render + Netlify)

```
Navegador ──► Netlify (frontend/)  ──/api/*──►  Render (FastAPI)  ──►  MongoDB Atlas (M0 gratis)
```

1. **MongoDB Atlas.** Crea un clúster gratuito M0, un usuario de base de datos y, en *Network Access*, permite `0.0.0.0/0`.
   Copia la cadena `mongodb+srv://...` y carga los datos desde tu equipo (la BD ocupa ~65 MB):
   ```powershell
   $env:MONGO_URI = "mongodb+srv://USUARIO:CLAVE@cluster0.xxxxx.mongodb.net"
   python -m etl.seed
   python -m ml.train
   ```
2. **Render (backend).** Sube el proyecto a GitHub, crea un *Blueprint* en Render apuntando al repositorio (usa `render.yaml`)
   y rellena `MONGO_URI` con la misma cadena. Comprueba `https://<tu-servicio>.onrender.com/api/health`.
3. **Netlify (frontend).** En `netlify.toml` sustituye `TU-BACKEND.onrender.com` por la URL de Render y despliega:
   ```powershell
   npx netlify-cli login
   npx netlify-cli deploy --prod --dir frontend
   ```
   La primera vez elige *Create & configure a new project*. También puedes importar el repositorio desde el panel de Netlify.

El plan gratuito de Render duerme el servicio tras 15 min sin uso: la primera carga puede tardar ~1 min.

---

## 2. Datos

### Fuentes

| Fuente | Ficheros | Uso |
|---|---|---|
| **Global Football Data Lake** de Transfermarkt ([salimt/football-datasets](https://github.com/salimt/football-datasets)) | `player_profiles`, `player_market_value`, `player_national_performances`, `team_competitions_seasons`, `player_performances` | Perfiles, nacionalidades, alturas, historial de valor, carrera internacional y control cruzado de campeones y goles |
| **Transfermarkt datasets** ([dcaribou/transfermarkt-datasets](https://github.com/dcaribou/transfermarkt-datasets), mismos `player_id`) | `appearances` (1,9 M de actuaciones por partido), `games`, `competitions`, `clubs`, `players`, `player_valuations` | Rendimiento partido a partido hasta el 28/06/2026, resultados y finales |

El snapshot del Data Lake es de septiembre de 2025, así que la temporada 2025-26 está incompleta en él. Por eso el rendimiento se calcula sumando partidos (dcaribou) y el Data Lake aporta perfiles y valores, además de servir de **verificación independiente**.

### Decisiones y hallazgos de depuración

- **Temporada = ventana del 1 de agosto al 31 de julio**, el periodo de evaluación del Balón de Oro. 2025-26 corresponde a la gala de 2026 y 2024-25 a la de 2025 (ya celebrada, sirve de validación).
- **Candidato** = al menos 1.200 minutos en la temporada. Salen unos 3.400 por temporada y 47.621 jugador-temporada en total.
- **`minutes_played` del Data Lake no son minutos**: son *minutos por gol* (1 gol en 22 partidos → 1580; vacío si no hay goles). No se usa; los minutos se suman desde los partidos.
- **Campeón de liga**: se calcula desde los resultados (3/1/0 puntos y desempate por diferencia de goles). Coincide con la clasificación oficial del Data Lake en **50 de 50** ligas top 5 (2015-2024).
- **Finales**: en `games` los goles ya incluyen la tanda de penaltis (Man City 6-5 Sevilla en la Supercopa 2023 es 1-1 y 5-4 en penaltis), así que el ganador sale del marcador.
- **Títulos de selección**: los partidos de selecciones solo existen desde 2025-12. Para galas anteriores, un título cuenta si la nacionalidad coincide con el campeón y el jugador tiene al menos 10 internacionalidades.
- **El `$jsonSchema` cazó dos errores reales durante la carga**: una altura de 19 cm en origen (corregida: las alturas fuera de 140-220 cm pasan a desconocidas) y 85 títulos de competiciones sin catálogo con nombre `NaN` (corregido: se usa el código de la competición).
- **Hasta 2021 el premio era por año natural**: para esas galas, la ventana agosto-julio es una aproximación.
- **Mundial 2026**: los datos llegan hasta el 28/06/2026, sin la fase final, así que nadie tiene ese título en 2025-26.

---

## 3. MongoDB

### Colecciones

| Colección | Tipo | Docs | Contenido |
|---|---|---|---|
| `player_seasons` | Normal, con `$jsonSchema` estricto | 47.621 | Un documento por jugador y temporada |
| `match_performances` | **TimeSeries** (`timeField: date`, `metaField: meta`, `granularity: hours`) | 237.082 | Cada partido de los candidatos en 2024-25 y 2025-26 |
| `market_values` | **TimeSeries** | 173.584 | Historial de tasaciones desde 2015 |
| `ballon_dor_history` | Normal | 36 | Podios reales 2013-2025 |
| `competitions` | Normal | 65 | Catálogo con nombres en español |
| `model_runs` | Normal | 1 por entrenamiento | Métricas, coeficientes y validación por temporada |

Documento de `player_seasons` (resumido):

```json
{
  "player_id": 937958, "season": 2025, "season_label": "2025-26",
  "name": "Lamine Yamal", "age": 19.0, "position": "Attack", "sub_position": "Right Winger",
  "club": {"id": 131, "name": "FC Barcelona"}, "league": {"id": "ES1", "name": "LaLiga", "country": "Spain"},
  "performance": {"apps": 48, "minutes": 3842, "goals": 25, "assists": 18, "ga": 43,
                  "goals_per90": 0.586, "ga_per90": 1.007, "yellow": 6, "red": 0, "top5_apps": 28,
                  "wins": 37, "draws": 4, "losses": 7, "win_rate": 0.7708},
  "ucl": {"apps": 10, "goals": 6, "assists": 4, "ga": 10},
  "national": {"apps": 3, "goals": 1, "assists": 0, "caps": 23, "career_goals": 6},
  "titles": [{"competition_id": "ES1", "name": "LaLiga", "weight": 3.0}, {"competition_id": "SUC", "name": "Supercopa de España", "weight": 0.5}],
  "title_points": 3.5, "market_value_eur": 200000000.0,
  "ballon_dor_rank": null,
  "ml": {"probability": 0.0081, "raw_score": 0.0383, "rank": 8, "model": "logistic_regression"}
}
```

### Validación JSON Schema

`etl/schema.py → PLAYER_SEASON_SCHEMA`, con `validationLevel: strict` y `validationAction: error`:

- **Obligatorios:** `player_id`, `season`, `season_label`, `name`, `position`, `age`, `club`, `league`, `performance` (con `apps`, `minutes`, `goals`, `assists`, `ga`), `title_points` y `market_value_eur`.
- **Tipos:** `int`/`long` para los contadores, `double` para ratios y `date` para fechas.
- **Rangos:** `goals` de 0 a 150, `minutes` de 0 a 12.000, `age` de 15 a 50, `height_cm` de 140 a 220, `win_rate` y `ml.probability` de 0 a 1, `market_value_eur` hasta 500 M€.
- **Enums:** `position` (Attack, Midfield, Defender, Goalkeeper) y `foot`. `season_label` debe cumplir el patrón `^[0-9]{4}-[0-9]{2}$`.

**Limitación de MongoDB:** las colecciones TimeSeries no admiten `validator`, ni en `create` (*'timeseries' is not allowed with 'validator'*) ni con `collMod` (*option not supported on a time-series collection*). Su integridad la garantiza el modelo Pydantic `MatchIn` del backend, con los mismos rangos (goles 0-10, minutos 0-130, resultado W/D/L).

### Índices y demostración con `explain("executionStats")`

| Índice | Colección | Claves | Lo usa |
|---|---|---|---|
| `uniq_player_season` | player_seasons | `{player_id: 1, season: 1}` único | Upserts y fichas |
| `goals_assists` | player_seasons | `{"performance.goals": -1, "performance.assists": -1}` | Máximos goleadores |
| `market_value` | player_seasons | `{market_value_eur: -1}` | Más valiosos |
| `season_league_position_age` | player_seasons | `{season, "league.id", position, age}` (orden ESR) | Filtros del dashboard |
| `season_club` | player_seasons | `{season: 1, "club.id": 1}` | Filtro por club |
| `season_ml_probability` | player_seasons | `{season: 1, "ml.probability": -1}` | Ranking por probabilidad |
| `name_search` | player_seasons | `{name_search: 1}` | Búsqueda por prefijo |
| `player_date` | match_performances | `{"meta.player_id": 1, date: -1}` | Timeline del jugador |
| `player_date` | market_values | `{"meta.player_id": 1, date: 1}` | Historial de valor |

`python -m scripts.explain_indexes` borra cada índice, ejecuta `explain`, lo recrea y repite. Resultado real (`docs/explain_report.md`; los JSON completos están en `docs/explain/`):

| Consulta | Sin índice | Docs examinados | Con índice | Claves / docs examinados | ms |
|---|---|---|---|---|---|
| Goleadores (≥25 goles, orden G, A) | **COLLSCAN** + SORT en memoria | 47.621 | **IXSCAN** `goals_assists` | 20 / 20 | 49 → 0 |
| Valor ≥ 100 M€ | **COLLSCAN** + SORT | 47.621 | **IXSCAN** `market_value` | 133 / 133 | 48 → 1 |
| Temporada + liga + posición + edad | IXSCAN `season_club` (solo el prefijo) | 3.440 | **IXSCAN** `season_league_position_age` | 13 / 13 | 13 → 1 |
| Pipeline de ranking ordenado por ML | IXSCAN + **SORT en memoria** | 3.440 | IXSCAN `season_ml_probability`, **sin SORT** | 3.440 / 3.440 | 76 → 73 |
| TimeSeries: partidos de un jugador | **COLLSCAN** | 78.836 buckets | **IXSCAN** `player_date` | 37 / 37 buckets | 72 → 2 |

En el caso 3, sin su índice el planificador no cae a COLLSCAN: aprovecha el prefijo `season` de otro índice. El índice compuesto reduce los documentos examinados de 3.440 a 13. En el caso 4 el ranking necesita toda la temporada, así que lo que ahorra el índice es el SORT bloqueante en memoria.

### Pipeline de agregación: Puntuación Balón de Oro

`backend/pipelines.py`. Primero, `season_maxima_pipeline` calcula los máximos de la temporada (`$match` + `$group`). Después, `ranking_pipeline` recorre estas etapas:

| Etapa | Qué hace |
|---|---|
| `$match` | Temporada y filtros, en el orden del índice ESR |
| `$sort` (opcional) | Ordena por `ml.probability` antes de puntuar, para que use el índice |
| `$addFields` | Calcula los 6 componentes normalizados (0-1) |
| `$addFields` | `bdo.score`: suma ponderada × 100 |
| `$addFields` | `bdo.tier` con `$switch` |
| `$sort` | Ordena por `bdo.score` |
| `$facet` | Ejecuta en paralelo las cinco sub-consultas que siguen |
| ↳ `leaderboard` | `$limit` + `$project` |
| ↳ `tiers` | `$bucket` con límites [0, 25, 40, 55, 100.1] → Resto, Aspirantes, Contendientes, Favoritos, con `$topN` de los 5 mejores de cada grupo |
| ↳ `promesas` | Sub-21 (`$match` + `$sort` + `$limit`) |
| ↳ `market_bands` | `$bucketAuto` en 5 franjas de valor de mercado |
| ↳ `summary` | `$group` con candidatos, goles y puntuación máxima |

| Componente | Peso | Cálculo |
|---|---|---|
| Goles + asistencias | 25 % | G+A / máximo de la temporada |
| Títulos | 25 % | puntos de títulos / máximo |
| Valor de mercado | 15 % | √(valor / máximo) |
| Champions | 15 % | G+A en Champions / máximo |
| G+A por 90 | 10 % | min(G+A/90, 1,5) / 1,5 |
| Victorias | 10 % | % de victorias del equipo |

Puntos de títulos: Champions y Mundial 5; Eurocopa y Copa América 4; liga top 5 3; Copa África 2,5; Europa League 2; otra liga y Mundial de Clubes 1,5; copa nacional 1; supercopas 0,5.

Los máximos son de toda la temporada, así que filtrar no cambia la puntuación de nadie. En la TimeSeries se usan `$setWindowFields` (G+A acumulado y forma de los últimos 5 partidos) y `$dateTrunc` (agregación mensual para la comparativa).

### Operaciones de escritura

- **`POST /api/matches`**
  1. `insertOne` en la TimeSeries `match_performances`, tras validar con Pydantic.
  2. `updateOne` con **pipeline de actualización** y `upsert: true` sobre `{player_id, season}`. La primera etapa `$set` suma el partido a los contadores; la segunda recalcula `ga`, `goals_per90`, `ga_per90`, `win_rate` y `ucl.ga` de forma atómica. Si el jugador no tiene documento en esa temporada, se crea a partir de su perfil más reciente (`$ifNull` + `$literal`).
  3. Si el `$jsonSchema` rechaza el documento, se borra el partido insertado (compensación) y se responde 422.
  4. Se vuelve a puntuar la temporada con el modelo (`bulkWrite` de unas 3.400 operaciones `UpdateOne`).
- **`PATCH /api/players/{id}/seasons/{season}`**: `$set` del valor de mercado y/o `$addToSet` de un título + `$inc` de `title_points`. Usa un filtro `titles.competition_id: {$ne: ...}` para no sumar dos veces el mismo título (responde 409).

---

## 4. Guía de MongoDB Compass

1. **Conectar.** Abre Compass, pulsa *New connection*, pega la URI `mongodb://localhost:27017` y pulsa *Connect*. Con autenticación, el formato es `mongodb://usuario:contraseña@host:27017/?authSource=admin`. En la barra lateral aparece la base `balon_de_oro`.
2. **Ver las reglas de validación.** `balon_de_oro` → `player_seasons` → pestaña **Validation**. Se ve el `$jsonSchema` completo, con *Validation Action: Error* y *Validation Level: Strict*. Para comprobarlo, ve a *Documents → Add data → Insert document* e inserta `{"player_id": 1, "season": 2025, "name": "Test", "performance": {"goals": "muchos"}}`. Compass mostrará *Document failed validation* con la regla incumplida.
3. **Inspeccionar las TimeSeries.** `match_performances` muestra en la barra lateral el icono de reloj de TimeSeries. Para ver solo los partidos de un jugador ordenados por fecha:
   - *Documents*, filtro: `{"meta.player_id": 937958}`
   - *Sort*: `{"date": -1}`

   En la pestaña *Indexes* aparece `player_date`. Las opciones de la colección (`timeField`, `metaField`, `granularity`) se ven en la información de la colección o con `db.getCollectionInfos({name: "match_performances"})` en la shell integrada (*>_MONGOSH*). En la pestaña *Aggregations* puedes pegar `player_timeline_pipeline` (de `backend/pipelines.py`) para ver las ventanas deslizantes.
4. **Explain Plan.** En `player_seasons → Explain Plan`, escribe el filtro `{"performance.goals": {"$gte": 25}}` y el sort `{"performance.goals": -1, "performance.assists": -1}`, y pulsa *Explain*. La vista visual muestra **IXSCAN** sobre `goals_assists`, con *Documents Examined ≈ Documents Returned* e *Is Covered Query* (si proyectas solo campos del índice). Para ver el COLLSCAN, ve a *Indexes*, oculta el índice `goals_assists` (*Hide Index*) y repite el Explain: aparecen **COLLSCAN** y un SORT en memoria con unos 47.621 documentos examinados. Después vuelve a mostrarlo con *Unhide*.
5. **Explain de una agregación.** En *Aggregations*, pega el pipeline de ranking con un `$match: {"season": 2025}` inicial y pulsa *Explain*: la etapa `$cursor` usa IXSCAN.
6. **Importar el fichero único.** Consulta "Cargar la base de datos desde un único fichero" en la sección 1.

---

## 5. API REST

Base: `http://localhost:8000`. Todas las respuestas son JSON. Swagger en `/docs`.

| Método y ruta | Parámetros | Operación MongoDB | Índice / rendimiento esperado |
|---|---|---|---|
| `GET /api/health` | — | `ping`, `serverInfo` | — |
| `GET /api/meta` | `season` | 3 × `aggregate` (`$match` + `$group`) sobre ligas, clubes y edades; `distinct("position")`; `findOne` en `model_runs` ordenado por `created_at` | IXSCAN por el prefijo `season` (unos 3.400 docs) |
| `GET /api/rankings` | `season`, `league`, `position`, `club`, `age_min`, `age_max`, `sort=score\|ml`, `limit` | `aggregate(season_maxima)` (en caché) + `aggregate(ranking_pipeline)` con `$facet`/`$bucket`/`$bucketAuto` | IXSCAN `season_league_position_age` (13 docs con todos los filtros); con `sort=ml`, IXSCAN `season_ml_probability` sin SORT en memoria |
| `GET /api/players/search` | `q`, `season`, `limit` | `find({season, name_search: {$regex: "^q"}})`, con respaldo `\bq` | IXSCAN acotado por el prefijo de la regex |
| `GET /api/players/{id}` | `season` | `aggregate` (`$match` + puntuación) en `player_seasons`; `aggregate` con `$setWindowFields` en la TimeSeries; `find` en `market_values`; `find` de la carrera | `uniq_player_season`; `player_date` en las dos TimeSeries |
| `GET /api/compare` | `ids` (hasta 6), `season` | `aggregate` con `$match: {player_id: {$in}}`; `aggregate` con `$dateTrunc` + `$group` + `$setWindowFields` en la TimeSeries | `uniq_player_season`; `player_date` |
| `GET /api/history` | — | `find` en `ballon_dor_history` ordenado por año y puesto; `findOne` por jugador | `year_rank`; `uniq_player_season` |
| `POST /api/matches` | Cuerpo `MatchIn` | `insertOne` en la TimeSeries; `updateOne` con pipeline y `upsert`; `bulkWrite` del re-scoring | `uniq_player_season` para el upsert |
| `PATCH /api/players/{id}/seasons/{season}` | Cuerpo `StatsPatch` | `updateOne` con `$set`/`$addToSet`/`$inc` y filtro anti-duplicados; `bulkWrite` del re-scoring | `uniq_player_season` |

Ejemplo de escritura:

```bash
curl -X POST http://localhost:8000/api/matches -H "Content-Type: application/json" -d '{
  "player_id": 937958, "date": "2026-05-20T20:00:00Z", "competition_id": "ES1",
  "goals": 2, "assists": 1, "minutes": 90, "result": "W"}'
```

Errores: **404** si el jugador o la temporada no existen; **409** si el título ya estaba; **422** si falla la validación de Pydantic o el `$jsonSchema` (se devuelven las reglas incumplidas).

---

## 6. Modelo predictivo (scikit-learn)

- **Datos:** 40.925 jugador-temporada de 12 galas (2013-2025, sin 2020). Positivo = podio real: 36 casos.
- **Features (21):** goles, asistencias, G+A/90, minutos, G+A y partidos en Champions, puntos de títulos, ganó Champions / liga top 5 / torneo de selecciones, % de victorias, log del valor, edad, liga top 5, percentiles dentro de la temporada (G+A, valor, títulos) y posición (one-hot).
- **Validación:** *Leave-One-Season-Out* (`LeaveOneGroupOut`), con el preprocesado dentro de un `Pipeline`: ninguna temporada participa en su propia evaluación.
- **Probabilidad de ganar:** un softmax por temporada sobre el score del modelo (solo hay un ganador por año). La temperatura (T = 1,129) se calibra maximizando la verosimilitud de los ganadores reales.
- Las temporadas con gala celebrada muestran la predicción **fuera de muestra**. La 2025-26 usa el modelo final.

| Modelo | Ganador en el 1.er puesto | En el top 3 | En el top 10 | Podio real en el top 10 | MRR |
|---|---|---|---|---|---|
| **Regresión logística (elegida)** | **50 %** | **75 %** | **92 %** | **83 %** | **0,650** |
| HistGradientBoosting | 17 % | 67 % | 83 % | 72 % | 0,421 |

Con solo 36 positivos, el boosting sobreajusta; el modelo lineal generaliza mejor. El fallo más claro es Modrić en 2018 (puesto 50): un centrocampista sin goles cuyo mérito, el Mundial 2018, no se refleja bien en los datos de clubes. Predicciones fuera de muestra para la gala de 2025: Raphinha 1.º, **Dembélé 2.º (ganó)**, Vitinha 8.º (3.º real).

**¿Y `transformers`?** No se usa a propósito. El problema es tabular, con 36 positivos: un modelo de lenguaje no aporta señal y añadiría varios GB de dependencias. La regresión logística es interpretable (sus coeficientes están en `model_runs`) y está validada temporada a temporada.

---

## 7. Frontend

`frontend/` (HTML, CSS y JS sin build, Chart.js 4.4.1 desde cdnjs), servido por FastAPI en `/`. La estética es la de la gala en el Théâtre du Châtelet: terciopelo azul, oro viejo, paneles de cristal, *Cormorant Garamond* para nombres y cifras e *Instrument Sans* para la interfaz.

- **Podio:** medallones 2-1-3 con la puntuación o la probabilidad grabada. Es la única animación de la página y respeta `prefers-reduced-motion`.
- **Ranking:** tabla o tarjetas, con distintivo de podio real en las galas pasadas, categorías (`$bucket`) y promesas sub-21.
- **Filtros:** temporada, liga, posición, club, rango de edad y orden por puntuación o por probabilidad.
- **Comparativa:** radar de los 6 componentes, G+A acumulado mes a mes (TimeSeries) y barras. Muestra el top 5 o los jugadores marcados en la tabla.
- **Ficha del jugador** (diálogo modal): desglose de la puntuación, forma partido a partido, valor de mercado, títulos y últimos partidos.
- **Formularios:** registrar un partido (con autocompletado accesible por teclado) y actualizar el valor o los títulos.
- **"¿Acierta el modelo?":** los podios reales frente al puesto que dio el modelo.
- **Responsive hasta 390 px** sin scroll horizontal, con foco visible y etiquetas ARIA.
