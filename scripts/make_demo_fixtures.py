"""
Genere des fixtures de DEMONSTRATION rejouables hors ligne.

ATTENTION : ces donnees sont SYNTHETIQUES. Elles respectent le schema reel
de l'API (observe en juillet 2026 : loyers en centimes, adresses avec code
postal, occupationModes, etc.) mais ne proviennent pas du site.

Elles servent a deux choses :
  1. faire tourner la suite de tests sans acces reseau ;
  2. demontrer le bot (blog / portfolio) quand le site est inaccessible.

Pour obtenir de VRAIES fixtures quand l'acces au site est retabli :
    python scripts/capture_fixtures.py
qui enregistre les reponses brutes de l'API dans fixtures/.

Lancement :
    .venv/Scripts/python.exe scripts/make_demo_fixtures.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))   # layout plat : crous.py est a la racine

from crous import PAGE_SIZE

OUT = ROOT / "fixtures"

# Deux campagnes actives, comme observe en juillet 2026 (42 flow, 47 residual).
CONTEXT = {
    "tools": {
        "currentSchoolYear": {"id": 42, "mechanism": "flow", "isEnabled": True},
        "nextSchoolYear": {"id": 47, "mechanism": "residual", "isEnabled": True},
        # Une campagne desactivee : le bot doit l'ignorer.
        "previousSchoolYear": {"id": 38, "mechanism": "flow", "isEnabled": False},
    }
}


def item(id_, residence, address, rent_cents, area=18, label="T1",
         low_stock=False, high_demand=False):
    """Fabrique un item au format brut de l'API."""
    return {
        "id": id_,
        "label": label,
        "residence": {"label": residence, "address": address},
        "occupationModes": [{"rent": {"min": rent_cents}}],
        "area": {"min": area},
        "available": True,
        "lowStock": low_stock,
        "highDemand": high_demand,
    }


# --- Campagne 42 : du 94 (la zone surveillee) + du bruit hors zone ---------
ITEMS_42 = [
    item(1001, "Residence Camille Claudel", "3 rue Camille Claudel, 94400 Vitry-sur-Seine",
         32150, area=19, low_stock=True),
    item(1002, "Residence Gabriel Peri", "12 av. Gabriel Peri, 94200 Ivry-sur-Seine",
         29900, area=17),
    item(1003, "Residence Universitaire de Creteil", "5 rue du Lac, 94000 Creteil",
         34500, area=21, high_demand=True),
    # Hors zone : doit etre filtre quand postal_prefix="94"
    item(1004, "Residence Jean Zay", "2 rue Jean Zay, 92230 Gennevilliers", 31000),
    item(1005, "Residence Bastille", "8 bd Richard Lenoir, 75011 Paris", 45000),
]

# --- Campagne 47 : les trois villes temoins + un doublon inter-campagnes ---
ITEMS_47 = [
    item(2001, "Residence des Landes", "5 rue Alsace-Lorraine, 40000 Mont-de-Marsan",
         27350, area=20),
    item(2002, "Residence Pasteur", "Quartier Porette, 20250 Corte", 25800, area=22),
    item(2003, "Residence Talleyrand", "8 av. Gambetta, 24000 Perigueux", 26400,
         area=19, low_stock=True),
    item(2004, "Residence Le Clos", "17 rue de la Paix, 94800 Villejuif", 33200,
         area=18),
    # Meme id que dans la campagne 42 -> doit etre dedoublonne
    item(1001, "Residence Camille Claudel", "3 rue Camille Claudel, 94400 Vitry-sur-Seine",
         32150, area=19),
    # Adresse sans code postal exploitable -> commune "?", filtre en zone 94
    item(2005, "Residence Inconnue", "lieu-dit Les Sables", 30000),
]


def page(items):
    return {"results": {"items": items}}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    (OUT / "context.json").write_text(
        json.dumps(CONTEXT, ensure_ascii=False, indent=2), encoding="utf-8")

    (OUT / "search_42_p1.json").write_text(
        json.dumps(page(ITEMS_42), ensure_ascii=False, indent=2), encoding="utf-8")

    (OUT / "search_47_p1.json").write_text(
        json.dumps(page(ITEMS_47), ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Fixtures de demonstration ecrites dans {OUT}")
    for f in sorted(OUT.glob("*.json")):
        print(f"  {f.name}")

    # --- Jeu dedie a la pagination : 250 logements du 94 sur 3 pages -------
    pag = OUT / "pagination"
    pag.mkdir(parents=True, exist_ok=True)
    (pag / "context.json").write_text(
        json.dumps({"tools": {"currentSchoolYear":
                              {"id": 99, "mechanism": "flow", "isEnabled": True}}},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    total = 250
    for p in range(1, 4):
        debut = (p - 1) * PAGE_SIZE
        lot = [item(9000 + i, f"Residence {i}",
                    f"{i} rue de Test, 94400 Vitry-sur-Seine", 30000)
               for i in range(debut, min(debut + PAGE_SIZE, total))]
        (pag / f"search_99_p{p}.json").write_text(
            json.dumps(page(lot), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Jeu pagination ({total} logements sur 3 pages) dans {pag}")


if __name__ == "__main__":
    main()
