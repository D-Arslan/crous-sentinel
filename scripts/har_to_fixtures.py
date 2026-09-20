"""
Extrait des fixtures rejouables depuis un export HAR du navigateur.

Pourquoi : le site refuse les clients automatises (HTTP 429 immediat), mais
le navigateur de l'utilisateur, lui, recoit parfaitement les donnees. Plutot
que de contourner cette protection, on recupere les reponses que le
navigateur a DEJA telechargees, via un export HAR de l'onglet Reseau.

Aucune requete n'est emise vers le site : on lit un fichier local.

Le HAR peut contenir des cookies de session : il reste sur la machine, et
seuls les corps JSON des appels API en sont extraits. Pense a supprimer le
HAR une fois les fixtures generees.

Lancement :
    python scripts/har_to_fixtures.py mon_export.har
    python scripts/har_to_fixtures.py mon_export.har --dest fixtures/real
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

DEST_DEFAUT = Path(__file__).resolve().parent.parent / "fixtures" / "real"


def _corps(entry: dict) -> str | None:
    """Renvoie le corps texte d'une reponse HAR, ou None."""
    content = (entry.get("response") or {}).get("content") or {}
    text = content.get("text")
    if not text:
        return None
    if content.get("encoding") == "base64":
        import base64
        try:
            return base64.b64decode(text).decode("utf-8", errors="replace")
        except Exception:
            return None
    return text


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    har_path = Path(sys.argv[1])
    dest = DEST_DEFAUT
    if "--dest" in sys.argv:
        dest = Path(sys.argv[sys.argv.index("--dest") + 1])

    if not har_path.exists():
        print(f"Fichier introuvable : {har_path}")
        sys.exit(1)

    try:
        har = json.loads(har_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"HAR illisible : {e}")
        sys.exit(1)

    entries = (har.get("log") or {}).get("entries") or []
    print(f"{len(entries)} requete(s) dans le HAR.")

    dest.mkdir(parents=True, exist_ok=True)
    trouves = {"context": 0, "search": 0}
    compteur_par_tool: dict[str, int] = {}

    for e in entries:
        url = (e.get("request") or {}).get("url") or ""
        if "/api/" not in url:
            continue
        corps = _corps(e)
        if not corps:
            continue
        try:
            data = json.loads(corps)
        except Exception:
            continue   # pas du JSON -> on ignore

        if "global/context" in url:
            (dest / "context.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  [contexte] {url}")
            trouves["context"] += 1

        elif "/search/" in url:
            id_tool = url.rstrip("/").split("/")[-1].split("?")[0]
            n = compteur_par_tool.get(id_tool, 0) + 1
            compteur_par_tool[id_tool] = n
            nb = len(((data.get("results") or {}).get("items")) or [])
            nom = f"search_{id_tool}_p{n}.json"
            (dest / nom).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  [recherche] {nom} : {nb} logement(s)")
            trouves["search"] += 1

    print()
    if not trouves["context"]:
        print("ATTENTION : aucune reponse /api/global/context trouvee.")
        print("Recharge la page avec l'onglet Reseau ouvert, puis re-exporte.")
    if not trouves["search"]:
        print("ATTENTION : aucune reponse /api/fr/search/<id> trouvee.")
        print("Verifie que l'export HAR inclut bien le CONTENU des reponses.")
    if trouves["context"] and trouves["search"]:
        print(f"Fixtures ecrites dans {dest}")
        print("Rejeu :")
        print(f"  python diagnostic.py --rejeu {dest}")


if __name__ == "__main__":
    main()
