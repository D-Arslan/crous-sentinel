"""
Diagnostic CROUS Sentinel — UN SEUL tour, aucune notification Telegram,
aucune ecriture dans seen.json.

But : trancher la question laissee ouverte depuis l'origine. Le fichier
seen.json est vide depuis le premier jour. Deux explications possibles :
  (a) le Val-de-Marne est reellement reste sans offre sur toute la periode ;
  (b) la chaine recherche -> parsing -> filtre est cassee et ne detecte rien.

Ce script cherche sur TOUTE la France, sans filtre de code postal, puis
verifie la presence de villes ou des disponibilites ont ete constatees de
visu (temoins). Si les temoins remontent, la chaine est saine et
l'explication est (a).

Deux modes :
    python scripts/diagnostic.py            -> en direct (necessite l'acces au site)
    python scripts/diagnostic.py --rejeu    -> hors ligne, sur les fixtures
    python scripts/diagnostic.py --rejeu fixtures/real
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))   # layout plat : crous.py est a la racine

from crous import fetch_annonces, replay_annonces, BOX_FRANCE, FIXTURES_DIR, log

# Villes ou des disponibilites ont ete constatees manuellement (16/09/2026).
# Servent de temoins : le bot DOIT les voir s'il fonctionne.
TEMOINS = {
    "40000": "Mont-de-Marsan",
    "20250": "Corte",
    "24000": "Perigueux",
}


def main() -> None:
    rejeu = "--rejeu" in sys.argv
    if rejeu:
        i = sys.argv.index("--rejeu")
        dossier = Path(sys.argv[i + 1]) if len(sys.argv) > i + 1 else FIXTURES_DIR
        log("========== DIAGNOSTIC (mode REJEU, hors ligne) ==========")
        log(f"Fixtures : {dossier}")
        status, annonces = replay_annonces(dossier, postal_prefix=None)
    else:
        log("========== DIAGNOSTIC (France entiere, sans filtre) ==========")
        log("Un seul tour, aucune notification, aucune ecriture memoire.")
        status, annonces = fetch_annonces(headless=True, box=BOX_FRANCE,
                                          postal_prefix=None)

    log(f"status = {status}")
    if status == "rate_limited":
        log("ECHEC : le site limite toujours nos requetes (HTTP 429).")
        log("Rien a conclure. Reessayer plus tard.")
        return
    if status != "ok":
        log("ECHEC : la recherche n'a pas abouti (file d'attente, reseau...).")
        log("Rien a conclure sur la chaine de detection.")
        return

    log(f"TOTAL : {len(annonces)} logement(s) trouve(s).")

    if not annonces:
        log("")
        log("VERDICT : recherche aboutie mais ZERO logement. C'est anormal si")
        log("le site en affiche -> la chaine de detection est en cause.")
        return

    par_dept = collections.Counter((a["postal"] or "?")[:2] for a in annonces)

    log("")
    log("--- Repartition par departement (top 15) ---")
    for dept, n in par_dept.most_common(15):
        log(f"  {dept} : {n} logement(s)")

    n94 = par_dept.get("94", 0)
    log("")
    log(f"--- Val-de-Marne (94) : {n94} logement(s) ---")
    for a in annonces:
        if (a["postal"] or "").startswith("94"):
            log(f"  {a['commune']} | {a['residence']} | {a['rent_eur']} EUR")

    log("")
    log("--- Temoins (disponibilites constatees manuellement) ---")
    trouves = 0
    for code, ville in TEMOINS.items():
        matches = [a for a in annonces if (a["postal"] or "") == code]
        if matches:
            trouves += 1
            log(f"  [VU] {ville} ({code}) : {len(matches)} logement(s)")
            for a in matches[:3]:
                log(f"        {a['residence']} | {a['rent_eur']} EUR | "
                    f"{a['address']}")
        else:
            log(f"  [ABSENT] {ville} ({code}) : rien trouve")

    log("")
    log("========== VERDICT ==========")
    if trouves:
        log(f"La chaine de detection FONCTIONNE ({trouves}/{len(TEMOINS)} "
            f"temoins retrouves).")
        log(f"Un seen.json vide s'expliquerait donc par l'absence reelle")
        log(f"d'offres dans le 94 ({n94} ici), pas par un bug.")
    else:
        log("Aucun temoin retrouve alors que des logements remontent ailleurs.")
        log("La zone de recherche ou le filtre sont en cause -> verifier la")
        log("bounding box et le parsing du code postal.")
    if rejeu:
        log("")
        log("NB : mode rejeu sur fixtures. Valide la CHAINE DE TRAITEMENT,")
        log("pas l'acces reseau ni la fraicheur des donnees.")


if __name__ == "__main__":
    main()
