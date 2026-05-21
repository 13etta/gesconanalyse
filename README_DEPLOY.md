# LOFSelect Gescon Action V2.1 corrigée

Correction incluse :
- suppression du doublon _search_with_serpapi
- ajout de _search_with_duckduckgo
- ajout de lofselect_urls.csv comme cache local d’URLs validées
- priorité : cache local -> SerpAPI -> DuckDuckGo -> slug_guess
- find-and-extract ne tente plus d’extraire un slug_guess confidence 0.20
- /debug/config indique si le cache existe et combien de lignes il contient

## Déploiement Render

Build command:
pip install -r requirements.txt

Start command:
uvicorn main:app --host 0.0.0.0 --port $PORT

Variables d’environnement :
ACTION_API_KEY=ta-cle-secrete
SERPAPI_API_KEY=ta-cle-serpapi
PYTHON_VERSION=3.12.8

Après push :
Render → Manual Deploy → Clear build cache & deploy

Tests :
/health
/debug/config

Dans le GPT :
findLofselectUrl pour NOX DE CAZAOUS doit retourner source manual_validated confidence 1.0.
