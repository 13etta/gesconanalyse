# Gescon Archive Downloader — GitHub Actions

Archive prête à l’emploi pour télécharger les archives Gescon depuis GitHub Actions.

## Contenu

```text
.github/workflows/download_gescon_by_year.yml
.github/workflows/download_gescon_single_range.yml
scripts/gescon_archive_downloader.py
scripts/recherche_guinut_gescon.py
requirements_gescon.txt
README.md
.gitignore
```

## Utilisation recommandée

1. Crée un dépôt GitHub, par exemple `gescon-archives`.
2. Dézippe cette archive à la racine du dépôt.
3. Commit / push.
4. Va dans GitHub > Actions.
5. Lance `Download Gescon Archives By Year`.
6. Choisis le mode :
   - `all` : tout
   - `britanniques` : britannique / Setter / Pointer
   - `continentaux` : continentaux / Épagneul Breton / Braques / etc.
7. Télécharge les artifacts produits année par année.

## Pour Hervé Guinut

Lance le workflow par année avec :

```text
mode = continentaux
```

Le workflow exécute aussi `recherche_guinut_gescon.py` et produit :

```text
output_guinut_YYYY/resultats_guinut_gescon.csv
output_guinut_YYYY/resultats_guinut_gescon.xlsx
```

## Pourquoi par année ?

GitHub Actions a une limite de temps par job. Par année, si une année échoue, tu ne perds pas tout.

## Lancement local

```powershell
python -m pip install -r requirements_gescon.txt
python -m playwright install chromium

python scripts/gescon_archive_downloader.py --start 2014-08-01 --end 2026-05-29 --mode continentaux --out output --pdf-dir pdfs

python scripts/recherche_guinut_gescon.py --pdf-dir pdfs --out output_guinut
```

## Remarque importante

Les PDF ne doivent pas être commit dans GitHub. Ils sont produits comme artifacts GitHub Actions.
