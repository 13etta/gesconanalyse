#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Téléchargement archives Gescon depuis GitHub Actions ou PC local.

Objectif :
- ouvrir https://gescon.fr/archive.php ;
- tenter de renseigner une période ;
- collecter les liens PDF/catalogues ;
- filtrer selon le mode : all, britanniques, continentaux ;
- télécharger les PDF ;
- produire un index CSV/XLSX.

Usage local :
    python scripts/gescon_archive_downloader.py --start 2014-08-01 --end 2026-05-29 --mode all --out output --pdf-dir pdfs

GitHub Actions :
    voir .github/workflows/download_gescon_by_year.yml

Notes :
- Gescon peut changer son HTML. Le script est volontairement tolérant.
- Les archives Gescon semblent commencer au 01/08/2014.
- Ne pas committer les PDF dans GitHub. Utiliser les artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, List, Optional, Dict, Tuple
from urllib.parse import urljoin, urlparse, unquote

import pandas as pd
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from tqdm import tqdm


BASE_URL = "https://gescon.fr/"
ARCHIVE_URL = "https://gescon.fr/archive.php"


def strip_accents(value: str) -> str:
    value = "" if value is None else str(value)
    return "".join(c for c in unicodedata.normalize("NFD", value) if unicodedata.category(c) != "Mn")


def norm(value: str) -> str:
    value = strip_accents(value)
    value = value.upper()
    value = re.sub(r"\s+", " ", value).strip()
    return value


def is_pdf_like_url(url: str) -> bool:
    u = url.lower()
    return (
        ".pdf" in u
        or "cataloguepdf" in u
        or "catalogue" in u and "pdf" in u
        or "/mes-concurrents/" in u and "catalogue" in u.lower()
    )


def infer_mode_from_text(text: str) -> str:
    t = norm(text)
    if "CONTINENTAUX" in t or "CONTINENTAL" in t:
        return "continentaux"
    if "BRITANNIQUES" in t or "BRITANNIQUE" in t:
        return "britanniques"
    return "unknown"


def keep_by_mode(text: str, mode: str) -> bool:
    if mode == "all":
        return True
    inferred = infer_mode_from_text(text)
    if mode == "britanniques":
        return inferred == "britanniques"
    if mode == "continentaux":
        return inferred == "continentaux"
    return True


def safe_filename_from_url(url: str, fallback_prefix: str = "gescon") -> str:
    parsed = urlparse(url)
    name = Path(unquote(parsed.path)).name
    if not name or "." not in name:
        digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:10]
        name = f"{fallback_prefix}_{digest}.pdf"
    if not name.lower().endswith(".pdf"):
        name = name + ".pdf"
    name = re.sub(r'[<>:"/\\|?*]+', "_", name)
    name = re.sub(r"\s+", "_", name).strip("_")
    return name


@dataclass
class CatalogueLink:
    url: str
    text: str
    source_page: str
    mode_detected: str
    date_probable: str = ""
    lieu_probable: str = ""


def extract_date(text: str) -> str:
    for blob in [text or ""]:
        m = re.search(r"\b(20\d{2})[-_/\.](\d{2})[-_/\.](\d{2})\b", blob)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        m = re.search(r"\b(\d{2})[/\-\.](\d{2})[/\-\.](20\d{2})\b", blob)
        if m:
            return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return ""


def extract_links_from_html(html: str, source_url: str, mode: str) -> List[CatalogueLink]:
    soup = BeautifulSoup(html, "html.parser")
    out: List[CatalogueLink] = []

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        text = " ".join(a.get_text(" ", strip=True).split())
        absolute = urljoin(source_url, href)
        blob = f"{href} {text}"

        if not is_pdf_like_url(absolute):
            continue
        if not keep_by_mode(blob, mode):
            continue

        out.append(CatalogueLink(
            url=absolute,
            text=text or href,
            source_page=source_url,
            mode_detected=infer_mode_from_text(blob),
            date_probable=extract_date(blob),
            lieu_probable="",
        ))

    # Certains liens sont dans des attributs onclick/data-url.
    for tag in soup.find_all(True):
        attrs = " ".join(str(v) for v in tag.attrs.values())
        for m in re.finditer(r"""(?:https?://[^'"\s]+|[\w./-]*CataloguePDF[^'"\s]+|[\w./-]+\.pdf)""", attrs, flags=re.I):
            raw = m.group(0)
            absolute = urljoin(source_url, raw)
            blob = f"{raw} {tag.get_text(' ', strip=True)}"

            if not is_pdf_like_url(absolute):
                continue
            if not keep_by_mode(blob, mode):
                continue

            out.append(CatalogueLink(
                url=absolute,
                text=tag.get_text(" ", strip=True)[:200],
                source_page=source_url,
                mode_detected=infer_mode_from_text(blob),
                date_probable=extract_date(blob),
                lieu_probable="",
            ))

    # Dédoublonnage
    seen = set()
    dedup = []
    for item in out:
        if item.url in seen:
            continue
        seen.add(item.url)
        dedup.append(item)

    return dedup


def try_fill_archive_form(page, start: str, end: str, mode: str) -> None:
    """
    Remplit le formulaire Gescon de façon tolérante.
    Si les sélecteurs ne correspondent pas, le script continue et collecte ce qui est visible.
    """
    # Accepter cookies si bouton visible.
    for label in ["Accepter", "J'accepte", "OK", "Tout accepter"]:
        try:
            page.get_by_text(label, exact=False).click(timeout=1500)
            break
        except Exception:
            pass

    # Date inputs.
    inputs = page.locator("input")
    count = inputs.count()

    date_like_indices = []
    for i in range(count):
        try:
            inp = inputs.nth(i)
            typ = (inp.get_attribute("type") or "").lower()
            name = (inp.get_attribute("name") or "").lower()
            ident = (inp.get_attribute("id") or "").lower()
            placeholder = (inp.get_attribute("placeholder") or "").lower()
            blob = f"{typ} {name} {ident} {placeholder}"

            if typ == "date" or any(k in blob for k in ["date", "debut", "début", "start", "from", "fin", "end", "to"]):
                date_like_indices.append(i)
        except Exception:
            continue

    # Essayer d'abord type=date en ISO, puis format français si refus.
    if len(date_like_indices) >= 1:
        for value in [start, start.replace("-", "/")]:
            try:
                inputs.nth(date_like_indices[0]).fill(value, timeout=2000)
                break
            except Exception:
                pass

    if len(date_like_indices) >= 2:
        for value in [end, end.replace("-", "/")]:
            try:
                inputs.nth(date_like_indices[1]).fill(value, timeout=2000)
                break
            except Exception:
                pass

    # Select mode si possible.
    if mode in {"britanniques", "continentaux"}:
        selects = page.locator("select")
        for i in range(selects.count()):
            sel = selects.nth(i)
            try:
                options_text = norm(sel.inner_text(timeout=1000))
                wanted = "BRITANNIQUES" if mode == "britanniques" else "CONTINENTAUX"
                if wanted in options_text:
                    # Essais par label approximatif.
                    for label in [wanted.capitalize(), wanted, wanted.lower()]:
                        try:
                            sel.select_option(label=label, timeout=1000)
                            break
                        except Exception:
                            pass
            except Exception:
                pass

    # Cliquer bouton recherche.
    clicked = False
    for text in ["Rechercher", "Recherche", "Valider", "Afficher", "Chercher", "OK"]:
        try:
            page.get_by_text(text, exact=False).click(timeout=2000)
            clicked = True
            break
        except Exception:
            pass

    if not clicked:
        buttons = page.locator("button, input[type=submit]")
        for i in range(buttons.count()):
            try:
                buttons.nth(i).click(timeout=1000)
                break
            except Exception:
                pass

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    # Scroll pour charger contenu lazy.
    try:
        for _ in range(5):
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(800)
    except Exception:
        pass


def collect_links_playwright(start: str, end: str, mode: str, max_pages: int = 50) -> List[CatalogueLink]:
    links: List[CatalogueLink] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
        )
        page = context.new_page()
        page.goto(ARCHIVE_URL, wait_until="domcontentloaded", timeout=60000)

        try_fill_archive_form(page, start, end, mode)

        for page_idx in range(max_pages):
            html = page.content()
            links.extend(extract_links_from_html(html, page.url, mode))

            # Pagination : tenter bouton/liens suivants.
            next_clicked = False
            for label in ["Suivant", ">", "»", "Next"]:
                try:
                    locator = page.get_by_text(label, exact=True)
                    if locator.count() > 0:
                        locator.first.click(timeout=2000)
                        page.wait_for_load_state("networkidle", timeout=10000)
                        next_clicked = True
                        break
                except Exception:
                    pass

            if not next_clicked:
                # liens rel=next
                try:
                    next_href = page.locator('a[rel="next"]').first.get_attribute("href")
                    if next_href:
                        page.goto(urljoin(page.url, next_href), wait_until="networkidle", timeout=30000)
                        next_clicked = True
                except Exception:
                    pass

            if not next_clicked:
                break

        browser.close()

    # Dédoublonnage
    seen = set()
    dedup = []
    for item in links:
        if item.url in seen:
            continue
        seen.add(item.url)
        dedup.append(item)

    return dedup


def download_file(url: str, dest: Path, retries: int = 3) -> Tuple[bool, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "Mozilla/5.0 GesconArchiveBot/1.0"}

    if dest.exists() and dest.stat().st_size > 1000:
        return True, "already_exists"

    last_err = ""
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, headers=headers, timeout=60, stream=True) as r:
                r.raise_for_status()
                with dest.open("wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 128):
                        if chunk:
                            f.write(chunk)
            if dest.stat().st_size < 500:
                last_err = "download_too_small"
                continue
            return True, "downloaded"
        except Exception as e:
            last_err = repr(e)
            time.sleep(2 * attempt)

    return False, last_err


def main() -> int:
    parser = argparse.ArgumentParser(description="Télécharge les archives Gescon.")
    parser.add_argument("--start", required=True, help="Date début YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Date fin YYYY-MM-DD")
    parser.add_argument("--mode", choices=["all", "britanniques", "continentaux"], default="all")
    parser.add_argument("--out", default="output")
    parser.add_argument("--pdf-dir", default="pdfs")
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--no-download", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out)
    pdf_dir = Path(args.pdf_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)

    print(f"Période : {args.start} → {args.end}")
    print(f"Mode : {args.mode}")
    print("Collecte des liens Gescon...")

    links = collect_links_playwright(args.start, args.end, args.mode, args.max_pages)
    print(f"Liens catalogues/PDF trouvés : {len(links)}")

    index_rows = []
    for item in links:
        filename = safe_filename_from_url(item.url)
        dest = pdf_dir / filename

        status = "not_downloaded"
        ok = True
        err = ""

        if not args.no_download:
            ok, status = download_file(item.url, dest)
            if not ok:
                err = status
                status = "error"

        row = asdict(item)
        row.update({
            "filename": filename,
            "local_path": str(dest),
            "download_status": status,
            "download_error": err,
        })
        index_rows.append(row)

    df = pd.DataFrame(index_rows)
    csv_path = out_dir / "archive_index.csv"
    xlsx_path = out_dir / "archive_index.xlsx"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_excel(xlsx_path, index=False)

    print(f"Index CSV : {csv_path}")
    print(f"Index XLSX : {xlsx_path}")
    print("Terminé.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
