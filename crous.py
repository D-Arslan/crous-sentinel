"""
Module central CROUS : recherche fiable de logements via l'API interne du
site, en passant par un navigateur Playwright pour survivre a la file
d'attente ("Vous etes trop nombreux").

Aucune de ces fonctions ne doit faire planter l'appelant : en cas d'erreur,
on logge et on renvoie un echec explicite.

Revision du 16/09/2026 :
  - les appels API partent desormais DE LA PAGE (fetch dans le navigateur)
    et non plus de page.request, un client HTTP cote Node qui court-circuite
    le navigateur. C'etait un bug : la conception voulait explicitement que
    l'API soit interrogee depuis le navigateur, seul a franchir la file ;
  - detection explicite du HTTP 429 -> RateLimitedError ;
  - pagination : on ne se contente plus des 100 premiers resultats ;
  - zone de recherche parametrable (le 94 n'est plus code en dur) ;
  - MODE REJEU : la chaine parsing -> filtre est testable hors ligne, a
    partir de fixtures JSON, sans toucher au site (voir replay_annonces).
"""
from __future__ import annotations

import json
import re
import sys
import time
import traceback
from pathlib import Path

BASE = "https://trouverunlogement.lescrous.fr"
SEARCH_API = BASE + "/api/fr/search/{id_tool}"
CONTEXT_API = BASE + "/api/global/context"
ACCOMMODATION_URL = BASE + "/tools/{id_tool}/accommodations/{id_acc}"

# Bounding box couvrant TOUT le Val-de-Marne (94), avec une marge.
# On filtre ensuite precisement sur le code postal 94xxx.
BOX_94 = [{"lon": 2.30, "lat": 48.87}, {"lon": 2.63, "lat": 48.64}]

# Bounding box couvrant la France metropolitaine + la Corse (mode diagnostic).
BOX_FRANCE = [{"lon": -5.20, "lat": 51.10}, {"lon": 9.60, "lat": 41.30}]

# Communes prioritaires -> libelle lisible (le reste = "Val-de-Marne").
COMMUNES_94 = {
    "94400": "Vitry-sur-Seine",
    "94000": "Creteil",
    "94200": "Ivry-sur-Seine",
}

QUEUE_MARKER = "trop nombreux"

PAGE_SIZE = 100
MAX_PAGES = 20          # garde-fou : 2000 logements par campagne

FIXTURES_DIR = Path(__file__).with_name("fixtures")


class RateLimitedError(Exception):
    """Le serveur nous limite (HTTP 429). L'appelant DOIT reculer."""


def log(msg: str) -> None:
    """Logge une ligne horodatee, SANS JAMAIS planter.

    Les messages d'erreur de Playwright contiennent parfois des caracteres
    hors cp1252 (ex: la fleche U+2192 dans ses 'call log'). Sur une console
    Windows, un print brut leve alors UnicodeEncodeError -- et comme cet
    appel a lieu dans un gestionnaire d'exception, l'erreur d'origine est
    remplacee par un crash.
    """
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        enc = (getattr(sys.stdout, "encoding", None) or "utf-8")
        print(line.encode(enc, errors="replace").decode(enc, errors="replace"),
              flush=True)
    except Exception:
        pass  # le log ne doit jamais casser l'appelant


def _raise_if_rate_limited(status: int | None) -> None:
    """Transforme un 429 en exception explicite (au lieu d'un echec flou)."""
    if status == 429:
        raise RateLimitedError("HTTP 429 Too Many Requests")


# --------------------------------------------------------------------------
# Requetes DEPUIS le navigateur
# --------------------------------------------------------------------------
_FETCH_JS = """
async ({url, method, body}) => {
    const opts = {method, headers: {'Accept': 'application/json'}};
    if (body) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = body;
    }
    const r = await fetch(url, opts);
    const text = await r.text();
    return {status: r.status,
            ctype: r.headers.get('content-type') || '',
            text: text};
}
"""


def browser_fetch(page, url: str, method: str = "GET",
                  body: str | None = None) -> dict:
    """
    Execute une requete DEPUIS la page courante, via le fetch() du navigateur.

    Difference essentielle avec page.request : ici la requete part reellement
    du navigateur (meme pile reseau, meme session que la page qui a franchi
    la file d'attente). page.request est un client HTTP cote Node : il ne
    partage que les cookies, ce qui rendait la conception d'origine caduque.

    Renvoie {"status": int, "ctype": str, "text": str}.
    Leve RateLimitedError sur un 429.
    """
    res = page.evaluate(_FETCH_JS, {"url": url, "method": method, "body": body})
    _raise_if_rate_limited(res.get("status"))
    return res


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
    """Charge une URL en gerant la file d'attente. True si on est passe.
    Leve RateLimitedError si le serveur repond 429 (inutile d'insister)."""
    for attempt in range(1, max_tries + 1):
        try:
            resp = page.goto(url, timeout=60000, wait_until="domcontentloaded")
            _raise_if_rate_limited(resp.status if resp else None)
            page.wait_for_timeout(2000)
        except RateLimitedError:
            raise
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
# Lecture du contexte (campagnes actives) — logique PURE + acces reseau
# --------------------------------------------------------------------------
def parse_context(data: dict) -> list[dict]:
    """Extrait les campagnes actives d'une reponse /api/global/context.
    Fonction PURE : testable hors ligne."""
    tools = (data or {}).get("tools", {}) or {}
    out = []
    for key in ("currentSchoolYear", "nextSchoolYear"):
        t = tools.get(key)
        if isinstance(t, dict) and t.get("isEnabled") and "id" in t:
            out.append({"id": t["id"], "mechanism": t.get("mechanism", "")})
    return out


def get_active_tools(page, capture_dir: Path | None = None) -> list[dict]:
    """Renvoie les campagnes (idTool) actuellement actives a interroger."""
    try:
        res = browser_fetch(page, CONTEXT_API)
        if "application/json" not in res["ctype"]:
            log(f"  Contexte non-JSON (status={res['status']}, "
                f"content-type={res['ctype']!r})")
            return []
        data = json.loads(res["text"])
    except RateLimitedError:
        raise
    except Exception as e:
        log(f"  Impossible de lire le contexte: {e}")
        return []
    if capture_dir:
        _save_fixture(capture_dir, "context.json", data)
    return parse_context(data)


# --------------------------------------------------------------------------
# Recherche
# --------------------------------------------------------------------------
def build_payload(id_tool: int, mechanism: str, box, page_no: int = 1) -> dict:
    """Construit le corps de requete de l'API de recherche. Fonction PURE."""
    return {
        "idTool": id_tool, "need_aggregation": False,
        "page": page_no, "pageSize": PAGE_SIZE,
        "sector": None, "occupationModes": [], "location": box,
        "residence": None, "precision": 7, "equipment": [],
        "price": {"max": 10000000},
        "area": {"min": 0}, "adaptedPmr": False,
        "toolMechanism": mechanism,
    }


def search_tool(page, id_tool: int, mechanism: str, box=BOX_94,
                capture_dir: Path | None = None):
    """
    Interroge l'API pour une campagne, en parcourant TOUTES les pages.
    Renvoie la liste d'items, ou None en cas d'ECHEC.
    Leve RateLimitedError sur un 429.
    """
    items: list[dict] = []
    for page_no in range(1, MAX_PAGES + 1):
        payload = build_payload(id_tool, mechanism, box, page_no)
        try:
            res = browser_fetch(page, SEARCH_API.format(id_tool=id_tool),
                                method="POST", body=json.dumps(payload))
            if "application/json" not in res["ctype"]:
                log(f"  Reponse non-JSON pour idTool={id_tool} "
                    f"(file d'attente probable, status={res['status']})")
                return None if page_no == 1 else items
            data = json.loads(res["text"])
            batch = (data.get("results") or {}).get("items") or []
        except RateLimitedError:
            raise
        except Exception as e:
            log(f"  Erreur recherche idTool={id_tool} page={page_no}: {e}")
            return None if page_no == 1 else items

        if capture_dir:
            _save_fixture(capture_dir, f"search_{id_tool}_p{page_no}.json", data)

        items.extend(batch)
        if len(batch) < PAGE_SIZE:
            break          # derniere page atteinte
    else:
        log(f"  ATTENTION: garde-fou {MAX_PAGES} pages atteint pour "
            f"idTool={id_tool}, resultats peut-etre tronques.")
    return items


# --------------------------------------------------------------------------
# Parsing d'un item -> annonce simple (logique PURE)
# --------------------------------------------------------------------------
def _postal_and_city(address: str) -> tuple[str | None, str | None]:
    """Extrait le code postal ET le nom de commune (le texte qui suit le code
    postal dans l'adresse). Ex: '... 94800 Villejuif' -> ('94800',
    'Villejuif'). Renvoie (None, None) si rien de trouvable."""
    address = address or ""
    m = re.search(r"\b(\d{5})\b[\s,]*([^,\d]+)?", address)
    if m:
        city = (m.group(2) or "").strip().rstrip(".").strip()
        return m.group(1), (city or None)
    return None, None


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


def matches_zone(annonce: dict, postal_prefix: str | None) -> bool:
    """True si l'annonce est dans la zone. postal_prefix=None -> tout garder.
    Fonction PURE : c'est le filtre geographique du bot."""
    if postal_prefix is None:
        return True
    return bool(annonce["postal"] and annonce["postal"].startswith(postal_prefix))


# Marqueurs d'urgence : si UNE campagne les signale, l'annonce les porte.
_FLAGS_URGENCE = ("low_stock", "high_demand", "available")


def collect(items: list[dict], id_tool: int, postal_prefix: str | None,
            annonces: dict) -> int:
    """Parse des items bruts, filtre par zone, dedoublonne par id.
    Fonction PURE (mute 'annonces'). Partagee par le mode live ET le rejeu :
    c'est elle qui garantit que les tests valident le vrai chemin de code.

    Un meme logement peut apparaitre dans PLUSIEURS campagnes (ex: l'id 1001
    en 'flow' et en 'residual'). L'implementation d'origine faisait
    'annonces[id] = a', donc la derniere campagne traitee ecrasait la
    precedente -- et avec elle ses marqueurs 'stock faible' / 'tres demande'.
    On conserve donc la premiere occurrence et on fusionne les marqueurs
    d'urgence : ne jamais perdre un signal qui presse l'utilisateur.
    """
    kept = 0
    for it in items:
        a = parse_item(it, id_tool)
        if not matches_zone(a, postal_prefix):
            continue
        existante = annonces.get(a["id"])
        if existante is None:
            annonces[a["id"]] = a
        else:
            for flag in _FLAGS_URGENCE:
                existante[flag] = bool(existante.get(flag)) or bool(a.get(flag))
        kept += 1
    return kept


def is_in_94(annonce: dict) -> bool:
    return matches_zone(annonce, "94")


# --------------------------------------------------------------------------
# Fixtures (mode rejeu / capture)
# --------------------------------------------------------------------------
def _save_fixture(directory: Path, name: str, data) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"  [capture] {name}")
    except Exception as e:
        log(f"  [capture] echec pour {name}: {e}")


def replay_annonces(fixtures_dir: Path = FIXTURES_DIR, box=None,
                    postal_prefix: str | None = "94"):
    """
    Rejoue la chaine COMPLETE (contexte -> recherche -> parsing -> filtre)
    a partir de fixtures JSON, sans aucun acces reseau.

    Permet de tester et de demontrer le bot quand le site est inaccessible,
    et de valider que la detection fonctionne sur des donnees maitrisees.

    Renvoie (status, annonces), meme contrat que fetch_annonces.
    """
    fixtures_dir = Path(fixtures_dir)
    ctx_file = fixtures_dir / "context.json"
    if not ctx_file.exists():
        log(f"[rejeu] Fixture manquante : {ctx_file}")
        return "fail", []

    try:
        context = json.loads(ctx_file.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"[rejeu] context.json illisible : {e}")
        return "fail", []

    tools = parse_context(context)
    if not tools:
        log("[rejeu] Aucune campagne active dans la fixture de contexte.")
        return "fail", []
    log(f"[rejeu] Campagnes actives: {[t['id'] for t in tools]}")

    annonces: dict = {}
    for t in tools:
        items: list[dict] = []
        for page_no in range(1, MAX_PAGES + 1):
            f = fixtures_dir / f"search_{t['id']}_p{page_no}.json"
            if not f.exists():
                break
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                log(f"[rejeu] {f.name} illisible : {e}")
                return "fail", []
            items.extend((data.get("results") or {}).get("items") or [])
        kept = collect(items, t["id"], postal_prefix, annonces)
        zone = postal_prefix or "toutes zones"
        log(f"[rejeu]   idTool={t['id']}: {len(items)} logements, "
            f"{kept} retenus ({zone})")

    return "ok", list(annonces.values())


# --------------------------------------------------------------------------
# Point d'entree principal (live)
# --------------------------------------------------------------------------
def fetch_annonces(headless: bool = True, box=BOX_94,
                   postal_prefix: str | None = "94",
                   capture_dir: Path | None = None):
    """
    Ouvre un navigateur, passe la file d'attente, interroge toutes les
    campagnes actives, filtre par prefixe de code postal, dedoublonne.

    capture_dir : si fourni, enregistre les reponses brutes de l'API dans ce
    dossier (pour constituer des fixtures rejouables hors ligne).

    Renvoie (status, annonces) ou status vaut :
      - "ok"           : recherche aboutie (annonces peut etre vide = 0 dispo)
      - "rate_limited" : le serveur nous limite (429) -> reculer franchement
      - "fail"         : echec (file d'attente, reseau...) -> a ne PAS
                         interpreter comme "0 logement"
    """
    from playwright.sync_api import sync_playwright  # import tardif : le mode
    # rejeu et les tests n'ont pas besoin de Playwright.

    annonces: dict = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(locale="fr-FR")
            page = ctx.new_page()
            try:
                log("Ouverture du site (gestion file d'attente)...")
                if not load_through_queue(page, BASE + "/"):
                    log("Impossible de passer la file d'attente ce tour-ci.")
                    return "fail", []

                tools = get_active_tools(page, capture_dir=capture_dir)
                if not tools:
                    log("Aucune campagne active trouvee.")
                    return "fail", []
                log(f"Campagnes actives: {[t['id'] for t in tools]}")

                any_success = False
                for t in tools:
                    items = search_tool(page, t["id"], t["mechanism"], box=box,
                                        capture_dir=capture_dir)
                    if items is None:
                        continue
                    any_success = True
                    kept = collect(items, t["id"], postal_prefix, annonces)
                    zone = postal_prefix or "toutes zones"
                    log(f"  idTool={t['id']} ({t['mechanism']}): "
                        f"{len(items)} logements dans la box, {kept} retenus "
                        f"({zone})")

                if not any_success:
                    return "fail", []
                return "ok", list(annonces.values())
            finally:
                browser.close()
    except RateLimitedError as e:
        log(f"LIMITATION DE DEBIT detectee ({e}). On recule.")
        return "rate_limited", []
    except Exception:
        log("ERREUR inattendue pendant la recherche :")
        traceback.print_exc()
        return "fail", list(annonces.values())


def fetch_annonces_94(headless: bool = True):
    """Compatibilite : ancienne signature (ok: bool, annonces)."""
    status, annonces = fetch_annonces(headless=headless, box=BOX_94,
                                      postal_prefix="94")
    return status == "ok", annonces


if __name__ == "__main__":
    status, res = fetch_annonces()
    print("status =", status)
    for a in res:
        print(a)
