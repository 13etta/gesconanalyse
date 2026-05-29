#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Recherche Hervé Guinut / GUINUT dans tous les PDF téléchargés.

Usage :
    python scripts/recherche_guinut_gescon.py --pdf-dir pdfs --out output_guinut
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path
from typing import List, Dict, Any

import pandas as pd
import pdfplumber
from tqdm import tqdm


def strip_accents(value: str) -> str:
    value = "" if value is None else str(value)
    return "".join(c for c in unicodedata.normalize("NFD", value) if unicodedata.category(c) != "Mn")


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(value).upper()).strip()


def classify_pdf(filename: str, text: str) -> Dict[str, str]:
    blob = norm(filename + " " + (text or ""))

    if "CONTINENTAUX" in blob or "CONTINENTAL" in blob:
        groupe = "continentaux"
    elif "BRITANNIQUES" in blob or "BRITANNIQUE" in blob:
        groupe = "britanniques"
    else:
        groupe = "non_identifie"

    if "EPAGNEUL BRETON" in blob or "E.B" in blob or " EB " in blob:
        race = "epagneul_breton"
    elif "SETTER ANGLAIS" in blob or " S.A" in blob:
        race = "setter_anglais"
    elif "POINTER" in blob:
        race = "pointer"
    elif "BRAQUE" in blob:
        race = "braque"
    elif "KORTHALS" in blob:
        race = "griffon_korthals"
    else:
        race = "non_identifiee"

    if "BECASSINE" in blob:
        discipline = "becassine"
    elif "BECASSE" in blob:
        discipline = "becasse"
    elif "PRINTEMPS" in blob or "PERDRIX" in blob or "FTP" in blob:
        discipline = "printemps"
    elif "GIBIER NATUREL" in blob or "FGN" in blob:
        discipline = "gibier_naturel"
    elif "GIBIER SAUVAGE" in blob or "FGS" in blob:
        discipline = "gibier_sauvage"
    elif "GIBIER TIRE" in blob or "GIBIER TIRÉ" in blob or "FGT" in blob:
        discipline = "gibier_tire"
    else:
        discipline = "non_identifiee"

    return {"groupe_probable": groupe, "race_probable": race, "discipline_probable": discipline}


def extract_date(text: str) -> str:
    m = re.search(r"\b(20\d{2})[-_/\.](\d{2})[-_/\.](\d{2})\b", text or "")
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"\b(\d{2})[/\-\.](\d{2})[/\-\.](20\d{2})\b", text or "")
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return ""


def infer_lieu_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"^\d{4}[-_]\d{2}[-_]\d{2}[-_]", "", stem)
    stem = stem.replace("_", " ").replace("-", " ")
    stop = {"BRITANNIQUES", "BRITANNIQUE", "CONTINENTAUX", "CONTINENTAL", "SETTER", "ANGLAIS", "POINTER", "EPAGNEUL", "BRETON"}
    tokens = []
    for tok in stem.split():
        if norm(tok) in stop:
            break
        tokens.append(tok)
    return " ".join(tokens).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-dir", required=True)
    parser.add_argument("--out", default="output_guinut")
    parser.add_argument("--window", type=int, default=6)
    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    pdfs = sorted(pdf_dir.rglob("*.pdf"))

    for pdf_path in tqdm(pdfs, desc="Scan GUINUT"):
        try:
            with pdfplumber.open(str(pdf_path)) as pdf:
                first = pdf.pages[0].extract_text() if pdf.pages else ""
                meta = classify_pdf(pdf_path.name, first or "")
                date = extract_date(pdf_path.name + " " + (first or ""))
                lieu = infer_lieu_from_filename(pdf_path.name)

                for page_num, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text() or ""
                    if "GUINUT" not in norm(text):
                        continue
                    lines = [l.strip() for l in text.splitlines() if l.strip()]
                    for i, line in enumerate(lines):
                        if "GUINUT" in norm(line):
                            start = max(0, i - args.window)
                            end = min(len(lines), i + args.window + 1)
                            rows.append({
                                "source_pdf": str(pdf_path),
                                "filename": pdf_path.name,
                                "page": page_num,
                                "date_probable": date,
                                "lieu_probable": lieu,
                                **meta,
                                "ligne_match": line,
                                "contexte": "\n".join(lines[start:end]),
                            })
        except Exception as e:
            rows.append({
                "source_pdf": str(pdf_path),
                "filename": pdf_path.name,
                "page": "",
                "date_probable": "",
                "lieu_probable": "",
                "groupe_probable": "erreur",
                "race_probable": "",
                "discipline_probable": "",
                "ligne_match": f"ERREUR LECTURE PDF : {e}",
                "contexte": "",
            })

    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=[
            "source_pdf", "filename", "page", "date_probable", "lieu_probable",
            "groupe_probable", "race_probable", "discipline_probable",
            "ligne_match", "contexte"
        ])

    df.to_csv(out_dir / "resultats_guinut_gescon.csv", index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(out_dir / "resultats_guinut_gescon.xlsx", engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="occurrences", index=False)
        if not df.empty:
            synth = df.groupby(["groupe_probable", "race_probable", "discipline_probable"], dropna=False).size().reset_index(name="occurrences")
        else:
            synth = pd.DataFrame(columns=["groupe_probable", "race_probable", "discipline_probable", "occurrences"])
        synth.to_excel(writer, sheet_name="synthese", index=False)

    print(f"Occurrences GUINUT : {len(df)}")
    print(f"Sortie : {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
