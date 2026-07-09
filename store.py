"""
Memoire des annonces deja vues (etape 3).

Stocke dans un fichier JSON local les logements deja rencontres (par id),
pour ne notifier QUE les nouveautes. Concu pour ne jamais planter et ne
jamais corrompre le fichier (ecriture atomique).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

SEEN_PATH = Path(__file__).with_name("seen.json")


def load_seen(path: Path = SEEN_PATH) -> dict:
    """Charge la memoire. Renvoie {} si absent ou illisible (jamais d'erreur)."""
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[store] Fichier memoire illisible ({e}), on repart a vide.")
        return {}


def save_seen(data: dict, path: Path = SEEN_PATH) -> None:
    """Ecrit la memoire de facon ATOMIQUE (tmp + replace)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)  # atomique sur le meme volume
    except Exception as e:
        print(f"[store] Echec sauvegarde memoire: {e}")
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def mark_seen(seen: dict, a: dict) -> None:
    """Ajoute UNE annonce a la memoire (mutation en place). A appeler seulement
    apres notification reussie, pour pouvoir reessayer en cas d'echec d'envoi."""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    seen[str(a.get("id"))] = {
        "residence": a.get("residence"),
        "commune": a.get("commune"),
        "postal": a.get("postal"),
        "rent_eur": a.get("rent_eur"),
        "url": a.get("url"),
        "first_seen": now,
        "last_seen": now,
    }


def touch_seen(seen: dict, a: dict) -> None:
    """Rafraichit 'last_seen' d'une annonce deja connue (mutation en place)."""
    key = str(a.get("id"))
    if key in seen:
        seen[key]["last_seen"] = time.strftime("%Y-%m-%d %H:%M:%S")


def split_new(annonces: list[dict], seen: dict) -> tuple[list[dict], dict]:
    """
    Separe les annonces en (nouvelles, memoire_mise_a_jour).
    - nouvelles = celles dont l'id n'est pas deja en memoire.
    - la memoire est enrichie avec les nouvelles (+ maj 'last_seen' des connues).
    Ne modifie pas 'seen' en place : renvoie une copie.
    """
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    updated = dict(seen)
    nouvelles = []
    for a in annonces:
        key = str(a.get("id"))
        if key in updated:
            updated[key]["last_seen"] = now  # deja connue : on rafraichit
        else:
            nouvelles.append(a)
            updated[key] = {
                "residence": a.get("residence"),
                "commune": a.get("commune"),
                "postal": a.get("postal"),
                "rent_eur": a.get("rent_eur"),
                "url": a.get("url"),
                "first_seen": now,
                "last_seen": now,
            }
    return nouvelles, updated
