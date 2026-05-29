import pandas as pd
from pathlib import Path

files = list(Path(".").rglob("resultats_guinut_gescon.csv"))
dfs = []

for f in files:
    try:
        df = pd.read_csv(f)
        df["fichier_source_recherche"] = str(f)
        if not df.empty:
            dfs.append(df)
    except Exception as e:
        print("Erreur lecture", f, e)

if dfs:
    out = pd.concat(dfs, ignore_index=True)
else:
    out = pd.DataFrame(columns=[
        "source_pdf", "filename", "page", "date_probable", "lieu_probable",
        "groupe_probable", "race_probable", "discipline_probable",
        "ligne_match", "contexte", "fichier_source_recherche"
    ])

out.to_csv("resultats_guinut_gescon_global.csv", index=False, encoding="utf-8-sig")
out.to_excel("resultats_guinut_gescon_global.xlsx", index=False)

print("Fichiers CSV trouvés :", len(files))
print("Lignes fusionnées :", len(out))
print("Sorties créées :")
print("resultats_guinut_gescon_global.csv")
print("resultats_guinut_gescon_global.xlsx")
