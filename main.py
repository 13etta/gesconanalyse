from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import os, re, unicodedata, requests
from bs4 import BeautifulSoup

app = FastAPI(title="LOFSelect Gescon Action", version="1.0.0")
API_KEY = os.getenv("ACTION_API_KEY", "")

def check_key(x_api_key: Optional[str]):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(s or "")) if unicodedata.category(c) != "Mn")

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(s).upper()).strip()

def parse_date(s: str) -> str:
    m = re.search(r"(\d{2})[/-](\d{2})[/-](20\d{2})", s or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""

def normalize_qualif(s: str):
    t = norm(s)
    if "RCACIT" in t: q = "RCACIT"
    elif "CACIT" in t: q = "CACIT"
    elif "RCACT" in t: q = "RCACT"
    elif "CACT" in t: q = "CACT"
    elif "CQN" in t: q = "CQN"
    elif "EXCELLENT" in t or re.search(r"\bEXC\b", t): q = "EXCELLENT"
    elif "TRES BON" in t or re.search(r"\bTB\b", t): q = "TRES BON"
    elif "NON CLASSE" in t or re.search(r"\bNC\b", t): q = "NON CLASSE"
    elif "ELIMINE" in t or re.search(r"\bEL\b", t): q = "ELIMINE"
    elif ("PAS D" in t and "OCCASION" in t) or re.search(r"\bPO\b", t): q = "PAS D'OCCASION"
    elif "RETIRE" in t or re.search(r"\bRET\b", t): q = "RETIRE"
    elif "FORFAIT" in t or re.search(r"\bFORF\b", t): q = "FORFAIT"
    elif "BON" in t: q = "BON"
    else: q = "INFORMATION_INCOMPLETE"
    points = {"CACIT":100,"RCACIT":90,"CACT":80,"RCACT":70,"EXCELLENT":50,"TRES BON":30,"CQN":20,"BON":15}.get(q,0)
    result = 1 if q in {"CACIT","RCACIT","CACT","RCACT","EXCELLENT","TRES BON","CQN","BON"} else 0
    if q in {"CACIT","RCACIT","CACT","RCACT","EXCELLENT"}: statut = "excellent_resultat"
    elif q in {"TRES BON","CQN","BON"}: statut = "resultat_classe"
    elif q in {"NON CLASSE","PAS D'OCCASION"}: statut = "resultat_non_classe"
    elif q == "ELIMINE": statut = "elimine"
    elif q == "RETIRE": statut = "retire"
    elif q == "FORFAIT": statut = "absence"
    else: statut = "information_incomplete"
    return q, statut, points, 1, result

def discipline(s: str) -> str:
    t = norm(s)
    if "BECASSINE" in t: return "becassine"
    if "BECASSE" in t: return "becasse"
    if "GRANDE QUETE" in t or re.search(r"\bGQ\b", t): return "grande_quete"
    if "PRINTEMPS" in t or "PERDRIX" in t or re.search(r"\bFTP\b", t): return "printemps"
    if "GIBIER NATUREL" in t or re.search(r"\bFGN\b", t): return "gibier_naturel"
    if "GIBIER SAUVAGE" in t or re.search(r"\bFGS\b", t): return "gibier_sauvage"
    if "GIBIER TIRE" in t or "GIBIER TIRÉ" in t or re.search(r"\bFGT\b", t): return "gibier_tire"
    return "discipline_non_identifiee"

def category(s: str) -> str:
    t = norm(s)
    parts = []
    if "COUPLE" in t: parts.append("couple")
    if "SOLO" in t: parts.append("solo")
    if "OUVERT" in t: parts.append("ouvert")
    if "INTER" in t: parts.append("interclubs")
    if "SPECIAL" in t or "SPECIALE" in t: parts.append("speciale")
    return "_".join(parts) if parts else "categorie_non_identifiee"

def parse_lof_text(raw_text: str, nom_chien: str, source_url: str = ""):
    lines = [re.sub(r"\s+", " ", x).strip() for x in raw_text.replace("\r", "\n").split("\n") if x.strip()]
    rows, warnings = [], []
    q_re = r"RCACIT|CACIT|RCACT|CACT|CQN|EXCELLENT|EXC|TRES BON|TB|NON CLASSE|ELIMINE|PAS D['’ ]OCCASION|FORFAIT|RETIRE|BON"
    i = 0
    while i < len(lines):
        d = parse_date(lines[i])
        if not d:
            i += 1
            continue
        block = lines[i:i+8]
        qi = next((j for j, x in enumerate(block) if re.search(q_re, norm(x))), -1)
        if qi < 0:
            warnings.append("Date sans qualificatif lisible: " + " | ".join(block[:4]))
            i += 1
            continue
        disc = block[1] if len(block) > 1 else ""
        lieu = block[2] if len(block) > 2 else ""
        qorig = block[qi]
        typec = block[qi+1] if qi+1 < len(block) else ""
        q, statut, pts, pres, res = normalize_qualif(qorig)
        rows.append({"nom_chien":nom_chien,"date_concours":d,"lieu":lieu,"discipline_source":disc,"discipline_normalisee":discipline(disc+" "+typec),"categorie":category(typec),"type_concours":typec,"qualificatif_original":qorig,"qualificatif_normalise":q,"statut_resultat":statut,"presentation_comptee":pres,"resultat_compte":res,"points_standardises":pts,"source_url":source_url,"raw_block":" | ".join(block)})
        i += max(1, qi + 2)
    return rows, warnings

def summarize(rows):
    counts = {}
    for r in rows:
        q = r["qualificatif_normalise"]
        counts[q] = counts.get(q, 0) + 1
    return {"nombre_presentations_lofselect":sum(r["presentation_comptee"] for r in rows),"nombre_resultats_lofselect":sum(r["resultat_compte"] for r in rows),"nombre_CACT":counts.get("CACT",0),"nombre_CACIT":counts.get("CACIT",0),"nombre_RCACT":counts.get("RCACT",0),"nombre_RCACIT":counts.get("RCACIT",0),"nombre_EXC":counts.get("EXCELLENT",0),"nombre_TB":counts.get("TRES BON",0),"nombre_CQN":counts.get("CQN",0),"nombre_non_classe":counts.get("NON CLASSE",0),"nombre_elimine":counts.get("ELIMINE",0),"nombre_forfait":counts.get("FORFAIT",0),"nombre_retire":counts.get("RETIRE",0),"nombre_pas_occasion":counts.get("PAS D'OCCASION",0),"qualificatifs":counts}

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

@app.get("/")
def root():
    return {"status":"ok","message":"API LOFSelect Gescon active. Use /health."}

@app.get("/health")
def health():
    return {"status":"ok","service":"LOFSelect Gescon Action"}

@app.post("/lofselect/parse-text")
def parse_text(req: ParseTextRequest, x_api_key: Optional[str] = Header(default=None)):
    check_key(x_api_key)
    rows, warnings = parse_lof_text(req.raw_text, req.nom_chien, req.source_url)
    return {"nom_chien": req.nom_chien, "rows": rows, "summary": summarize(rows), "warnings": warnings}

@app.post("/lofselect/extract-url")
def extract_url(req: ExtractUrlRequest, x_api_key: Optional[str] = Header(default=None)):
    check_key(x_api_key)
    try:
        r = requests.get(req.url, headers={"User-Agent":"Mozilla/5.0 SetterStatsBot"}, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script","style","noscript"]): tag.decompose()
        text = soup.get_text("\n")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Extraction URL impossible: {e}")
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
    statut = "valide_sources_coherentes" if pl and ecart == 0 else ("ecart_important" if abs(pct) > 20 else ("ecart_modere" if pl else "lofselect_manquant"))
    return {"nom_chien":req.nom_chien,"nombre_presentations_gescon":pg,"nombre_presentations_lofselect":pl,"nombre_presentations_final":final,"ecart_presentations":ecart,"ecart_pourcentage":pct,"statut_validation":statut,"commentaire_audit":"LOFSelect prime pour le comptage final; Gescon complète le contexte."}
