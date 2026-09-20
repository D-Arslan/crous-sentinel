"""
Capture de VRAIES fixtures depuis le site, pour rejeu hors ligne.

Fait UN SEUL tour et enregistre les reponses brutes de l'API dans
fixtures/real/. Ces fichiers permettent ensuite de rejouer et de tester
la chaine complete sans jamais retoucher au site (voir test_crous.py et
crous.replay_annonces).

C'est l'inverse de la logique qui a cause l'incident du 27/08 : au lieu
d'interroger le site en boucle, on capture une fois et on travaille hors
ligne autant qu'on veut.

Lancement (uniquement quand l'acces au site fonctionne) :
    .venv/Scripts/python.exe scripts/capture_fixtures.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))   # layout plat : crous.py est a la racine

from crous import fetch_annonces, BOX_FRANCE, log

DEST = ROOT / "fixtures" / "real"


def main() -> None:
    log("========== CAPTURE DE FIXTURES (un seul tour) ==========")
    log(f"Destination : {DEST}")

    status, annonces = fetch_annonces(headless=True, box=BOX_FRANCE,
                                      postal_prefix=None, capture_dir=DEST)

    log(f"status = {status}")
    if status == "rate_limited":
        log("Le site limite nos requetes. Rien capture, on n'insiste pas.")
        return
    if status != "ok":
        log("Tour non abouti. Rien de fiable a capturer.")
        return

    log(f"{len(annonces)} logement(s) captures.")
    fichiers = sorted(DEST.glob("*.json")) if DEST.exists() else []
    for f in fichiers:
        log(f"  {f.name} ({f.stat().st_size} octets)")
    log("")
    log("Rejeu hors ligne desormais possible :")
    log("  python -c \"from crous import replay_annonces; "
        "print(replay_annonces('fixtures/real', postal_prefix=None))\"")


if __name__ == "__main__":
    main()
