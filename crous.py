"""
Module central CROUS : recherche fiable des logements du Val-de-Marne (94)
via l'API interne du site, en passant par un navigateur Playwright pour
survivre a la file d'attente ("Vous etes trop nombreux") et aux protections.

Reutilise aux etapes 3 (memoire) et 4 (boucle + Telegram).

Aucune de ces fonctions ne doit faire planter l'appelant : en cas d'erreur,
on logge et on renvoie une liste vide.
"""
from __future__ import annotations

import json
import re
import time
import traceback

from playwright.sync_api import sync_playwright

BASE = "https://trouverunlogement.lescrous.fr"
SEARCH_API = BASE + "/api/fr/search/{id_tool}"
CONTEXT_API = BASE + "/api/global/context"
ACCOMMODATION_URL = BASE + "/tools/{id_tool}/accommodations/{id_acc}"

# Bounding box couvrant TOUT le Val-de-Marne (94), avec une marge.
# On filtre ensuite precisement sur le code postal 94xxx.
BOX_94 = [{"lon": 2.30, "lat": 48.87}, {"lon": 2.63, "lat": 48.64}]

# Communes prioritaires -> libelle lisible (le reste = "Val-de-Marne").
COMMUNES_94 = {
    "94400": "Vitry-sur-Seine",
    "94000": "Creteil",
    "94200": "Ivry-sur-Seine",
}

QUEUE_MARKER = "trop nombreux"


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# --------------------------------------------------------------------------
# File d'attente
# --------------------------------------------------------------------------
def _is_queue(page) -> bool:
    try:
        return QUEUE_MARKER in (page.title() or "").lower()
    except Exception:
        return True  # en cas de doute, on considere qu'on n'est pas passe


def load_through_queue(page, url: str, max_tries: int = 10,
                       wait_seconds: int = 20) -> bool:
    """Charge une URL en gerant la file d'attente. True si on est passe."""
    for attempt in range(1, max_tries + 1):
        try:
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
        except Exception as e:
            log(f"  goto a echoue (essai {attempt}): {e}")
            page.wait_for_timeout(wait_seconds * 1000)
            continue
        if not _is_queue(page):
            return True
        log(f"  File d'attente detectee (essai {attempt}/{max_tries}), "
            f"attente {wait_seconds}s...")
        page.wait_for_timeout(wait_seconds * 1000)
    return False


# --------------------------------------------------------------------------
# Appels API (via le contexte navigateur, deja passe la file)
# --------------------------------------------------------------------------
def get_active_tools(page) -> list[dict]:
    """Renvoie les campagnes (idTool) actuellement actives a interroger."""
    try:
        data = page.request.get(CONTEXT_API, timeout=30000).json()
        tools = data.get("tools", {})
    except Exception as e:
        log(f"  Impossible de lire le contexte: {e}")
        return []
    out = []
    for key in ("currentSchoolYear", "nextSchoolYear"):
        t = tools.get(key)
        if isinstance(t, dict) and t.get("isEnabled") and "id" in t:
            out.append({"id": t["id"], "mechanism": t.get("mechanism", "")})
    return out


def search_tool(page, id_tool: int, mechanism: str, box=BOX_94):
    """
    Interroge l'API pour une campagne.
    Renvoie la liste d'items (eventuellement vide) en cas de succes,
    ou None en cas d'ECHEC (file d'attente sur l'API, erreur reseau...).
    """
    payload = {
        "idTool": id_tool, "need_aggregation": False, "page": 1, "pageSize": 100,
        "sector": None, "occupationModes": [], "location": box, "residence": None,
        "precision": 7, "equipment": [], "price": {"max": 10000000},
        "area": {"min": 0}, "adaptedPmr": False, "toolMechanism": mechanism,
    }
    try:
        resp = page.request.post(
            SEARCH_API.format(id_tool=id_tool),
            data=json.dumps(payload),
            headers={"Content-Type": "application/json",
                     "Accept": "application/json"},
            timeout=30000,
        )
        if "application/json" not in resp.headers.get("content-type", ""):
            log(f"  Reponse non-JSON pour idTool={id_tool} "
                f"(file d'attente probable, status={resp.status})")
            return None
        return resp.json().get("results", {}).get("items", []) or []
    except Exception as e:
        log(f"  Erreur recherche idTool={id_tool}: {e}")
        return None


# --------------------------------------------------------------------------
# Parsing d'un item -> annonce simple
# --------------------------------------------------------------------------
def _postal_and_city(address: str) -> tuple[str | None, str | None]:
    """Extrait le code postal 94xxx ET le nom de commune (le texte qui suit
    le code postal dans l'adresse). Ex: '... 94800 Villejuif' -> ('94800',
    'Villejuif'). Renvoie (None, None) si rien de trouvable."""
    address = address or ""
    # priorite a un code postal du 94 suivi du nom de ville
    m = re.search(r"\b(94\d{3})\b[\s,]*([^,\d]+)?", address)
    if m:
        city = (m.group(2) or "").strip().rstrip(".").strip()
        return m.group(1), (city or None)
    # sinon, n'importe quel code postal (pour info), sans ville
    codes = re.findall(r"\b(\d{5})\b", address)
    return (codes[-1] if codes else None), None


def _min_rent_cents(item: dict) -> int | None:
    rents = []
    for mode in item.get("occupationModes") or []:
        r = (mode.get("rent") or {}).get("min")
        if isinstance(r, (int, float)):
            rents.append(r)
    return int(min(rents)) if rents else None


def parse_item(item: dict, id_tool: int) -> dict:
    res = item.get("residence") or {}
    address = res.get("address") or ""
    postal, city = _postal_and_city(address)
    # nom de commune : d'abord celui lu dans l'adresse, sinon la table connue,
    # sinon "Val-de-Marne" si c'est bien un 94.
    commune = city or COMMUNES_94.get(postal)
    if not commune:
        commune = "Val-de-Marne" if (postal and postal.startswith("94")) else "?"
    rent_cents = _min_rent_cents(item)
    area = item.get("area") or {}
    return {
        "id": item.get("id"),
        "id_tool": id_tool,
        "residence": res.get("label") or "Residence inconnue",
        "type": item.get("label") or "",
        "address": address,
        "postal": postal,
        "commune": commune,
        "rent_eur": round(rent_cents / 100, 2) if rent_cents is not None else None,
        "area_min": area.get("min"),
        "available": item.get("available", False),
        "low_stock": item.get("lowStock", False),
        "high_demand": item.get("highDemand", False),
        "url": ACCOMMODATION_URL.format(id_tool=id_tool, id_acc=item.get("id")),
    }


def is_in_94(annonce: dict) -> bool:
    return bool(annonce["postal"] and annonce["postal"].startswith("94"))


# --------------------------------------------------------------------------
# Point d'entree principal : recupere toutes les annonces 94
# --------------------------------------------------------------------------
def fetch_annonces_94(headless: bool = True):
    """
    Ouvre un navigateur, passe la file d'attente, interroge toutes les
    campagnes actives, filtre sur le 94, dedoublonne. Ne plante jamais.

    Renvoie un tuple (ok, annonces) :
      - ok = True  : la recherche a bien abouti (annonces peut etre vide = 0 dispo)
      - ok = False : ECHEC (file d'attente infranchissable, erreur...) -> a ne pas
                     interpreter comme "0 logement". annonces contient ce qu'on a pu.
    """
    annonces: dict[int, dict] = {}  # cle = id logement (dedoublonnage)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(locale="fr-FR")
            page = ctx.new_page()
            try:
                log("Ouverture du site (gestion file d'attente)...")
                if not load_through_queue(page, BASE + "/"):
                    log("Impossible de passer la file d'attente ce tour-ci.")
                    return False, []

                tools = get_active_tools(page)
                if not tools:
                    log("Aucune campagne active trouvee.")
                    return False, []
                log(f"Campagnes actives: {[t['id'] for t in tools]}")

                any_success = False
                for t in tools:
                    items = search_tool(page, t["id"], t["mechanism"])
                    if items is None:  # echec sur cette campagne
                        continue
                    any_success = True
                    kept = 0
                    for it in items:
                        a = parse_item(it, t["id"])
                        if is_in_94(a):
                            annonces[a["id"]] = a  # dedoublonne par id
                            kept += 1
                    log(f"  idTool={t['id']} ({t['mechanism']}): "
                        f"{len(items)} logements dans la box, {kept} dans le 94")

                if not any_success:
                    # toutes les campagnes ont echoue -> on ne sait rien de fiable
                    return False, []
                return True, list(annonces.values())
            finally:
                browser.close()
    except Exception:
        log("ERREUR inattendue pendant la recherche :")
        traceback.print_exc()
        return False, list(annonces.values())


if __name__ == "__main__":
    ok, res = fetch_annonces_94()
    print("ok =", ok)
    for a in res:
        print(a)
