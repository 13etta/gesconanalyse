from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from urllib.parse import quote_plus, urlparse, parse_qs, unquote
from pathlib import Path
import csv
import os
import re
import unicodedata
import requests
from bs4 import BeautifulSoup

app = FastAPI(title="LOFSelect Gescon Action", version="2.1.0")

API_KEY = os.getenv("ACTION_API_KEY", "").strip()
LOFSELECT_URLS_CSV = Path("lofselect_urls.csv")


# ============================================================
# Auth
# ============================================================

def check_key(x_api_key: Optional[str]):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ============================================================
# Normalisation
# ============================================================

def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", str(s or ""))
        if unicodedata.category(c) != "Mn"
    )


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(s).upper()).strip()


def parse_date(s: str) -> str:
    m = re.search(r"(\d{2})[/-](\d{2})[/-](20\d{2})", s or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def normalize_qualif(s: str):
    t = norm(s)

    if "RCACIT" in t:
        q = "RCACIT"
    elif "CACIT" in t:
        q = "CACIT"
    elif "RCACT" in t:
        q = "RCACT"
    elif "CACT" in t:
        q = "CACT"
    elif "CQN" in t:
        q = "CQN"
    elif re.search(r"\b[1-4]\s*(ER|E|EME)?\s*EXC", t):
        n = re.search(r"\b([1-4])", t).group(1)
        q = f"{n} EXCELLENT"
    elif "EXCELLENT" in t or re.search(r"\bEXC\b", t):
        q = "EXCELLENT"
    elif re.search(r"\b[1-4]\s*(ER|E|EME)?\s*(TRES BON|TB)", t):
        n = re.search(r"\b([1-4])", t).group(1)
        q = f"{n} TRES BON"
    elif "TRES BON" in t or re.search(r"\bTB\b", t):
        q = "TRES BON"
    elif "NON CLASSE" in t or re.search(r"\bNC\b", t):
        q = "NON CLASSE"
    elif "ELIMINE" in t or re.search(r"\bEL\b", t):
        q = "ELIMINE"
    elif ("PAS D" in t and "OCCASION" in t) or re.search(r"\bPO\b", t):
        q = "PAS D'OCCASION"
    elif "RETIRE" in t or re.search(r"\bRET\b", t):
        q = "RETIRE"
    elif "FORFAIT" in t or re.search(r"\bFORF\b", t):
        q = "FORFAIT"
    elif "BON" in t:
        q = "BON"
    else:
        q = "INFORMATION_INCOMPLETE"

    points = {
        "CACIT": 100,
        "RCACIT": 90,
        "CACT": 80,
        "RCACT": 70,
        "1 EXCELLENT": 60,
        "2 EXCELLENT": 55,
        "3 EXCELLENT": 52,
        "4 EXCELLENT": 50,
        "EXCELLENT": 50,
        "1 TRES BON": 35,
        "2 TRES BON": 33,
        "3 TRES BON": 31,
        "4 TRES BON": 30,
        "TRES BON": 30,
        "CQN": 20,
        "BON": 15,
    }.get(q, 0)

    result = 1 if q in {
        "CACIT", "RCACIT", "CACT", "RCACT",
        "1 EXCELLENT", "2 EXCELLENT", "3 EXCELLENT", "4 EXCELLENT",
        "EXCELLENT", "1 TRES BON", "2 TRES BON", "3 TRES BON", "4 TRES BON",
        "TRES BON", "CQN", "BON"
    } else 0

    if q in {"CACIT", "RCACIT", "CACT", "RCACT", "1 EXCELLENT", "2 EXCELLENT", "3 EXCELLENT", "4 EXCELLENT", "EXCELLENT"}:
        statut = "excellent_resultat"
    elif q in {"1 TRES BON", "2 TRES BON", "3 TRES BON", "4 TRES BON", "TRES BON", "CQN", "BON"}:
        statut = "resultat_classe"
    elif q in {"NON CLASSE", "PAS D'OCCASION"}:
        statut = "resultat_non_classe"
    elif q == "ELIMINE":
        statut = "elimine"
    elif q == "RETIRE":
        statut = "retire"
    elif q == "FORFAIT":
        statut = "absence"
    else:
        statut = "information_incomplete"

    return q, statut, points, 1, result


def discipline(s: str) -> str:
    t = norm(s)
    if "BECASSINE" in t:
        return "becassine"
    if "BECASSE" in t:
        return "becasse"
    if "GRANDE QUETE" in t or re.search(r"\bGQ\b", t):
        return "grande_quete"
    if "PRINTEMPS" in t or "PERDRIX" in t or re.search(r"\bFTP\b", t):
        return "printemps"
    if "GIBIER NATUREL" in t or re.search(r"\bFGN\b", t):
        return "gibier_naturel"
    if "GIBIER SAUVAGE" in t or re.search(r"\bFGS\b", t):
        return "gibier_sauvage"
    if "GIBIER TIRE" in t or "GIBIER TIRÉ" in t or re.search(r"\bFGT\b", t):
        return "gibier_tire"
    if "MONTAGNE" in t:
        return "montagne"
    return "discipline_non_identifiee"


def category(s: str) -> str:
    t = norm(s)
    parts = []
    if "COUPLE" in t:
        parts.append("couple")
    if "SOLO" in t:
        parts.append("solo")
    if "OUVERT" in t:
        parts.append("ouvert")
    if "INTER" in t:
        parts.append("interclubs")
    if "SPECIAL" in t or "SPECIALE" in t:
        parts.append("speciale")
    if "JEUNE" in t:
        parts.append("jeune")
    if "AMATEUR" in t:
        parts.append("amateur")
    return "_".join(parts) if parts else "categorie_non_identifiee"


# ============================================================
# Parsing LOFSelect texte
# ============================================================

def parse_lof_text(raw_text: str, nom_chien: str, source_url: str = ""):
    lines = [
        re.sub(r"\s+", " ", x).strip()
        for x in raw_text.replace("\r", "\n").split("\n")
        if x.strip()
    ]

    rows, warnings = [], []
    q_re = r"RCACIT|CACIT|RCACT|CACT|CQN|EXCELLENT|EXC|TRES BON|TB|NON CLASSE|ELIMINE|PAS D['’ ]OCCASION|FORFAIT|RETIRE|RETIRÉ|BON"

    i = 0
    while i < len(lines):
        d = parse_date(lines[i])
        if not d:
            i += 1
            continue

        block = lines[i:i + 8]
        qi = next((j for j, x in enumerate(block) if re.search(q_re, norm(x))), -1)

        if qi < 0:
            warnings.append("Date sans qualificatif lisible: " + " | ".join(block[:4]))
            i += 1
            continue

        disc = block[1] if len(block) > 1 else ""
        lieu = block[2] if len(block) > 2 else ""
        qorig = block[qi]
        typec = block[qi + 1] if qi + 1 < len(block) else ""

        q, statut, pts, pres, res = normalize_qualif(qorig)

        rows.append({
            "nom_chien": nom_chien,
            "date_concours": d,
            "lieu": lieu,
            "discipline_source": disc,
            "discipline_normalisee": discipline(disc + " " + typec),
            "categorie": category(typec),
            "type_concours": typec,
            "qualificatif_original": qorig,
            "qualificatif_normalise": q,
            "statut_resultat": statut,
            "presentation_comptee": pres,
            "resultat_compte": res,
            "points_standardises": pts,
            "source_url": source_url,
            "raw_block": " | ".join(block),
        })

        i += max(1, qi + 2)

    return deduplicate_lof_rows(rows), warnings


def deduplicate_lof_rows(rows: List[Dict[str, Any]]):
    """
    Dédoublonne les mentions liées même chien/date/lieu/discipline/catégorie.
    CACT + CACIT / CACT + RCACIT / RCACT + RCACIT = 1 présentation.
    """
    grouped = {}
    for r in rows:
        key = (
            norm(r.get("nom_chien", "")),
            r.get("date_concours", ""),
            norm(r.get("lieu", "")),
            r.get("discipline_normalisee", ""),
            r.get("categorie", ""),
        )
        grouped.setdefault(key, []).append(r)

    out = []
    high = {"CACIT", "RCACIT", "CACT", "RCACT"}

    for _, group in grouped.items():
        if len(group) == 1:
            x = dict(group[0])
            x["is_duplicate"] = 0
            x["is_barrage"] = 0
            x["mentions_associees"] = x.get("qualificatif_normalise", "")
            out.append(x)
            continue

        qs = sorted(set(g.get("qualificatif_normalise", "") for g in group))
        x = sorted(group, key=lambda z: z.get("points_standardises", 0), reverse=True)[0].copy()
        x["is_duplicate"] = 1
        x["is_barrage"] = 1 if len(set(qs) & high) >= 2 else 0
        x["mentions_associees"] = ",".join(qs)
        x["presentation_comptee"] = 1
        x["resultat_compte"] = 1 if any(g.get("resultat_compte", 0) for g in group) else 0
        x["raw_block"] = " || ".join(g.get("raw_block", "") for g in group)
        out.append(x)

    return sorted(out, key=lambda r: (r.get("nom_chien", ""), r.get("date_concours", ""), r.get("lieu", "")))


def summarize(rows):
    counts = {}
    for r in rows:
        q = r["qualificatif_normalise"]
        counts[q] = counts.get(q, 0) + 1

    return {
        "nombre_presentations_lofselect": sum(r["presentation_comptee"] for r in rows),
        "nombre_resultats_lofselect": sum(r["resultat_compte"] for r in rows),
        "nombre_CACT": counts.get("CACT", 0),
        "nombre_CACIT": counts.get("CACIT", 0),
        "nombre_RCACT": counts.get("RCACT", 0),
        "nombre_RCACIT": counts.get("RCACIT", 0),
        "nombre_EXC": sum(v for k, v in counts.items() if "EXCELLENT" in k),
        "nombre_TB": sum(v for k, v in counts.items() if "TRES BON" in k),
        "nombre_CQN": counts.get("CQN", 0),
        "nombre_non_classe": counts.get("NON CLASSE", 0),
        "nombre_elimine": counts.get("ELIMINE", 0),
        "nombre_forfait": counts.get("FORFAIT", 0),
        "nombre_retire": counts.get("RETIRE", 0),
        "nombre_pas_occasion": counts.get("PAS D'OCCASION", 0),
        "qualificatifs": counts,
    }


# ============================================================
# Models
# ============================================================

class ParseTextRequest(BaseModel):
    nom_chien: str
    raw_text: str
    source_url: str = ""


class ExtractUrlRequest(BaseModel):
    url: str
    nom_chien: str = ""


class ReconcileRequest(BaseModel):
    nom_chien: str
    gescon_summary: Dict[str, Any]
    lofselect_summary: Dict[str, Any]


class FindUrlRequest(BaseModel):
    nom_chien: str
    max_results: int = 5


class FindUrlCandidate(BaseModel):
    nom_chien: str
    url_identite: str
    url_utilisations: str
    confidence: float
    source: str
    note: str = ""


class FindUrlResponse(BaseModel):
    nom_chien: str
    candidates: List[FindUrlCandidate]
    warnings: List[str] = []


class FindAndExtractRequest(BaseModel):
    nom_chien: str
    max_results: int = 5


# ============================================================
# Routes base
# ============================================================

@app.get("/")
def root():
    return {"status": "ok", "message": "API LOFSelect Gescon active. Use /health."}


@app.get("/health")
def health():
    return {"status": "ok", "service": "LOFSelect Gescon Action"}


@app.get("/debug/config")
def debug_config():
    serp_key = os.getenv("SERPAPI_API_KEY", "").strip()
    action_key = os.getenv("ACTION_API_KEY", "").strip()

    return {
        "serpapi_key_present": bool(serp_key),
        "serpapi_key_length": len(serp_key),
        "action_key_present": bool(action_key),
        "action_key_length": len(action_key),
        "url_cache_exists": LOFSELECT_URLS_CSV.exists(),
        "url_cache_rows": len(_load_url_cache()) if LOFSELECT_URLS_CSV.exists() else 0,
    }


@app.post("/lofselect/parse-text")
def parse_text(req: ParseTextRequest, x_api_key: Optional[str] = Header(default=None)):
    check_key(x_api_key)
    rows, warnings = parse_lof_text(req.raw_text, req.nom_chien, req.source_url)
    return {"nom_chien": req.nom_chien, "rows": rows, "summary": summarize(rows), "warnings": warnings}


@app.post("/lofselect/extract-url")
def extract_url(req: ExtractUrlRequest, x_api_key: Optional[str] = Header(default=None)):
    check_key(x_api_key)
    try:
        r = requests.get(
            req.url,
            headers={"User-Agent": "Mozilla/5.0 SetterStatsBot"},
            timeout=30
        )
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        text = soup.get_text("\n")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Extraction URL impossible: {repr(e)}")

    rows, warnings = parse_lof_text(text, req.nom_chien, req.url)
    return {"nom_chien": req.nom_chien, "source_url": req.url, "rows": rows, "summary": summarize(rows), "warnings": warnings}


@app.post("/reconcile/chien")
def reconcile(req: ReconcileRequest, x_api_key: Optional[str] = Header(default=None)):
    check_key(x_api_key)

    pg = int(req.gescon_summary.get("nombre_presentations_gescon", 0) or 0)
    pl = int(req.lofselect_summary.get("nombre_presentations_lofselect", 0) or 0)

    final = pl if pl else pg
    ecart = pl - pg
    pct = round((ecart / pg * 100), 2) if pg else 0

    if not pl:
        statut = "lofselect_manquant"
    elif abs(pct) > 20:
        statut = "ecart_important"
    elif ecart:
        statut = "ecart_modere"
    else:
        statut = "valide_sources_coherentes"

    return {
        "nom_chien": req.nom_chien,
        "nombre_presentations_gescon": pg,
        "nombre_presentations_lofselect": pl,
        "nombre_presentations_final": final,
        "ecart_presentations": ecart,
        "ecart_pourcentage": pct,
        "statut_validation": statut,
        "commentaire_audit": "LOFSelect prime pour le comptage final; Gescon complète le contexte.",
    }


# ============================================================
# Cache local URL LOFSelect
# ============================================================

def _load_url_cache():
    """
    Charge les URLs LOFSelect validées depuis lofselect_urls.csv.
    Colonnes attendues :
    nom_chien,url_identite,url_utilisations,source,confidence
    """
    if not LOFSELECT_URLS_CSV.exists():
        return []

    rows = []

    try:
        with LOFSELECT_URLS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                nom = row.get("nom_chien", "").strip()
                ident = row.get("url_identite", "").strip()
                util = row.get("url_utilisations", "").strip()
                source = row.get("source", "cache_csv").strip() or "cache_csv"

                try:
                    confidence = float(row.get("confidence", "1.0") or 1.0)
                except Exception:
                    confidence = 1.0

                if not nom or not ident:
                    continue

                if not util:
                    util = ident.rstrip("/") + "/utilisations"

                rows.append({
                    "nom_chien": nom,
                    "url_identite": ident,
                    "url_utilisations": util,
                    "source": source,
                    "confidence": confidence,
                })
    except Exception:
        return []

    return rows


def _search_with_local_cache(nom_chien: str, max_results: int):
    wanted = norm(nom_chien)
    rows = _load_url_cache()
    candidates = []

    for row in rows:
        cached_name = norm(row.get("nom_chien", ""))

        if cached_name == wanted:
            candidates.append(
                FindUrlCandidate(
                    nom_chien=nom_chien,
                    url_identite=row["url_identite"],
                    url_utilisations=row["url_utilisations"],
                    confidence=float(row.get("confidence", 1.0)),
                    source=row.get("source", "cache_csv"),
                    note="URL LOFSelect validée depuis le cache local.",
                )
            )

    return candidates[:max_results], []


# ============================================================
# Recherche URL LOFSelect
# ============================================================

def _slug_name_for_lofselect(name: str) -> str:
    s = strip_accents(name).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def _canonical_lofselect_url(url: str) -> str:
    """
    Retourne l'URL identité canonique :
    https://www.centrale-canine.fr/lofselect/chien/nom-du-chien-1234567
    """
    if not url:
        return ""

    url = str(url).strip()

    if "uddg=" in url:
        qs = parse_qs(urlparse(url).query)
        if "uddg" in qs and qs["uddg"]:
            url = unquote(qs["uddg"][0])

    url = url.split("#")[0].split("?")[0]
    url = re.sub(r"/utilisations/?$", "", url)
    url = re.sub(r"/genealogie/?$", "", url)
    url = re.sub(r"/production/?$", "", url)

    m = re.search(
        r"https?://(?:www\.)?centrale-canine\.fr/lofselect/chien/[^/\s]+",
        url
    )

    if not m:
        return ""

    ident = m.group(0)
    ident = ident.replace("http://www.centrale-canine.fr", "https://www.centrale-canine.fr")
    ident = ident.replace("https://centrale-canine.fr", "https://www.centrale-canine.fr")
    ident = ident.replace("http://centrale-canine.fr", "https://www.centrale-canine.fr")

    return ident


def _candidate_from_url(nom_chien: str, url: str, confidence: float, source: str, note: str = ""):
    ident = _canonical_lofselect_url(url)
    if not ident:
        return None

    return FindUrlCandidate(
        nom_chien=nom_chien,
        url_identite=ident,
        url_utilisations=ident.rstrip("/") + "/utilisations",
        confidence=confidence,
        source=source,
        note=note,
    )


def _search_with_serpapi(nom_chien: str, max_results: int):
    key = os.getenv("SERPAPI_API_KEY", "").strip()

    if not key:
        return [], ["SERPAPI_API_KEY absente : recherche SerpAPI ignorée."]

    queries = [
        f'site:centrale-canine.fr/lofselect/chien "{nom_chien}"',
        f'"{nom_chien}" "LOFSelect"',
        f'"{nom_chien}" "centrale-canine"',
        f'{nom_chien} centrale canine lofselect',
    ]

    candidates = []
    warnings = []
    seen = set()

    for query in queries:
        try:
            r = requests.get(
                "https://serpapi.com/search.json",
                params={
                    "engine": "google",
                    "q": query,
                    "api_key": key,
                    "num": max_results,
                },
                timeout=25,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            warnings.append(f"Erreur SerpAPI pour requête [{query}] : {repr(e)}")
            continue

        if not isinstance(data, dict):
            warnings.append(f"Réponse SerpAPI non exploitable pour [{query}]")
            continue

        if data.get("error"):
            warnings.append(f"Erreur SerpAPI pour [{query}] : {data.get('error')}")
            continue

        organic = data.get("organic_results") or []
        if not organic:
            warnings.append(f"Aucun organic_result SerpAPI pour [{query}]")
            continue

        for item in organic[:max_results]:
            if not isinstance(item, dict):
                continue

            link = item.get("link") or item.get("redirect_link") or ""
            title = item.get("title") or ""
            snippet = item.get("snippet") or ""

            try:
                cand = _candidate_from_url(
                    nom_chien,
                    link,
                    0.95,
                    "serpapi_google",
                    f"{title} | {snippet}",
                )
            except Exception as e:
                warnings.append(f"Erreur parsing candidat SerpAPI [{link}] : {repr(e)}")
                continue

            if cand and cand.url_identite not in seen:
                seen.add(cand.url_identite)
                candidates.append(cand)

        if candidates:
            break

    if not candidates:
        warnings.append("SerpAPI active mais aucun résultat LOFSelect vérifié trouvé.")

    return candidates[:max_results], warnings


def _search_with_duckduckgo(nom_chien: str, max_results: int):
    """
    Fallback gratuit si SerpAPI ne trouve rien.
    """
    query = f'site:centrale-canine.fr/lofselect/chien "{nom_chien}"'
    search_url = "https://duckduckgo.com/html/?q=" + quote_plus(query)

    try:
        r = requests.get(
            search_url,
            headers={"User-Agent": "Mozilla/5.0 SetterStatsBot"},
            timeout=25,
        )
        r.raise_for_status()
    except Exception as e:
        return [], [f"Erreur DuckDuckGo : {repr(e)}"]

    soup = BeautifulSoup(r.text, "html.parser")
    candidates = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")

        try:
            ident = _canonical_lofselect_url(href)
        except Exception:
            continue

        if not ident or ident in seen:
            continue

        seen.add(ident)

        cand = _candidate_from_url(
            nom_chien=nom_chien,
            url=ident,
            confidence=0.70,
            source="duckduckgo_html",
            note="Candidat trouvé par fallback DuckDuckGo ; vérifier l’homonymie.",
        )

        if cand:
            candidates.append(cand)

        if len(candidates) >= max_results:
            break

    if not candidates:
        return [], ["DuckDuckGo actif mais aucun résultat LOFSelect vérifié trouvé."]

    return candidates, []


@app.post("/lofselect/find-url", response_model=FindUrlResponse)
def find_lofselect_url(req: FindUrlRequest, x_api_key: Optional[str] = Header(default=None)):
    check_key(x_api_key)

    warnings = []
    candidates = []

    # 0. Cache local validé
    cache_candidates, cache_warnings = _search_with_local_cache(req.nom_chien, req.max_results)
    candidates.extend(cache_candidates)
    warnings.extend(cache_warnings)

    if candidates:
        return FindUrlResponse(
            nom_chien=req.nom_chien,
            candidates=candidates[:req.max_results],
            warnings=warnings,
        )

    # 1. SerpAPI
    serp_candidates, serp_warnings = _search_with_serpapi(req.nom_chien, req.max_results)
    candidates.extend(serp_candidates)
    warnings.extend(serp_warnings)

    # 2. DuckDuckGo si SerpAPI ne trouve rien
    if not candidates:
        ddg_candidates, ddg_warnings = _search_with_duckduckgo(req.nom_chien, req.max_results)
        candidates.extend(ddg_candidates)
        warnings.extend(ddg_warnings)

    # 3. Fallback slug faible
    if not candidates:
        slug = _slug_name_for_lofselect(req.nom_chien)
        fallback = f"https://www.centrale-canine.fr/lofselect/chien/{slug}"

        candidates.append(
            FindUrlCandidate(
                nom_chien=req.nom_chien,
                url_identite=fallback,
                url_utilisations=fallback + "/utilisations",
                confidence=0.20,
                source="slug_guess",
                note="Piste faible : LOFSelect ajoute souvent un identifiant numérique à l’URL.",
            )
        )

        warnings.append("Aucune URL vérifiée trouvée. Candidat slug fourni à titre indicatif seulement.")

    dedup = []
    seen = set()

    for c in candidates:
        if c.url_identite in seen:
            continue
        seen.add(c.url_identite)
        dedup.append(c)

    return FindUrlResponse(
        nom_chien=req.nom_chien,
        candidates=dedup[:req.max_results],
        warnings=warnings,
    )


@app.post("/lofselect/find-and-extract")
def find_and_extract_lofselect(req: FindAndExtractRequest, x_api_key: Optional[str] = Header(default=None)):
    check_key(x_api_key)

    found = find_lofselect_url(
        FindUrlRequest(nom_chien=req.nom_chien, max_results=req.max_results),
        x_api_key=x_api_key,
    )

    if not found.candidates:
        return {
            "nom_chien": req.nom_chien,
            "status": "url_not_found",
            "warnings": found.warnings,
            "rows": [],
            "summary": {},
        }

    best = sorted(found.candidates, key=lambda c: c.confidence, reverse=True)[0]

    # Sécurité : ne pas tenter une extraction sur un slug faible.
    if best.confidence < 0.70:
        return {
            "nom_chien": req.nom_chien,
            "selected_url": best.url_utilisations,
            "selected_confidence": best.confidence,
            "selected_source": best.source,
            "candidates": [c.model_dump() for c in found.candidates],
            "status": "url_not_verified",
            "warnings": found.warnings + [
                "URL non vérifiée : extraction directe non tentée. Fournir l’URL exacte ou copier-coller le texte LOFSelect."
            ],
            "rows": [],
            "summary": {},
        }

    try:
        extracted = extract_url(
            ExtractUrlRequest(url=best.url_utilisations, nom_chien=req.nom_chien),
            x_api_key=x_api_key,
        )

        return {
            "nom_chien": req.nom_chien,
            "selected_url": best.url_utilisations,
            "selected_confidence": best.confidence,
            "selected_source": best.source,
            "candidates": [c.model_dump() for c in found.candidates],
            "warnings": found.warnings,
            "extraction": extracted,
        }

    except Exception as e:
        return {
            "nom_chien": req.nom_chien,
            "selected_url": best.url_utilisations,
            "selected_confidence": best.confidence,
            "selected_source": best.source,
            "candidates": [c.model_dump() for c in found.candidates],
            "status": "url_found_but_extraction_failed",
            "error": str(e),
            "warnings": found.warnings + [
                "URL trouvée mais extraction directe impossible. Copier-coller le texte LOFSelect et utiliser parseLofselectText."
            ],
        }
