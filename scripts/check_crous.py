"""
Outil manuel : affiche dans la console les logements CROUS du Val-de-Marne (94).
Aucune notification, aucune ecriture en memoire : juste un etat des lieux.

Lancement :
    .venv/Scripts/python.exe scripts/check_crous.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # crous.py, store.py sont a la racine

from crous import fetch_annonces_94, log  # noqa: E402


def format_annonce(a: dict) -> str:
    prix = f"{a['rent_eur']:.2f} EUR/mois" if a["rent_eur"] is not None else "prix ?"
    surface = f"{a['area_min']:.0f} m2" if a["area_min"] else "surface ?"
    flags = []
    if a["low_stock"]:
        flags.append("STOCK FAIBLE")
    if a["high_demand"]:
        flags.append("TRES DEMANDE")
    if not a["available"]:
        flags.append("indispo")
    flag_txt = ("  [" + ", ".join(flags) + "]") if flags else ""
    return (f"- {a['residence']} ({a['commune']}, {a['postal']})\n"
            f"    {a['type']} | {prix} | {surface}{flag_txt}\n"
            f"    {a['url']}")


def main() -> None:
    log("=== Recherche des logements CROUS du Val-de-Marne (94) ===")
    ok, annonces = fetch_annonces_94(headless=True)

    if not ok:
        log("!! Recherche NON aboutie (file d'attente ou erreur). Reessaie plus tard.")
        return

    log(f"=== {len(annonces)} logement(s) trouve(s) dans le 94 ===")
    if not annonces:
        print("\n(Aucun logement disponible dans le 94 pour le moment — "
              "c'est normal la plupart du temps.)")
        return

    # tri : communes prioritaires d'abord, puis par prix
    ordre_communes = ["Vitry-sur-Seine", "Creteil", "Ivry-sur-Seine"]

    def cle(a):
        try:
            i = ordre_communes.index(a["commune"])
        except ValueError:
            i = len(ordre_communes)
        return (i, a["rent_eur"] if a["rent_eur"] is not None else 1e9)

    print()
    for a in sorted(annonces, key=cle):
        print(format_annonce(a))
        print()


if __name__ == "__main__":
    main()
