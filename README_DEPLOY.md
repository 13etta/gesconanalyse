# LOFSelect Gescon Action V2 corrigée

Correction incluse :
- suppression du doublon `_search_with_serpapi`
- ajout de `_search_with_duckduckgo`
- `_canonical_lofselect_url` plus tolérant
- sécurité : pas d’extraction directe sur un slug faible confidence 0.20

## Render
Build command: `pip install -r requirements.txt`

Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

Variables : `ACTION_API_KEY`, `SERPAPI_API_KEY`, `PYTHON_VERSION=3.12.8`

Après push : Render → Manual Deploy → Clear build cache & deploy.

Tests : `/health`, `/debug/config`, `/lofselect/find-url`
