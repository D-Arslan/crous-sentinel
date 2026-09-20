"""
CROUS Sentinel — bot complet (boucle principale).

Boucle en continu :
  1. cherche les logements CROUS du 94 (via Playwright, gestion file d'attente),
  2. detecte les NOUVELLES annonces (memoire seen.json),
  3. envoie une notification Telegram pour chacune,
  4. attend ~15 min, puis recommence.

Concu pour tourner des jours sans s'arreter : toute erreur est loggee et le
bot continue. Arret propre avec Ctrl+C.

Revision du 16/09/2026, apres 17 jours d'aveuglement non detecte et une
limitation de debit (HTTP 429) infligee a l'IP du domicile :
  - rythme nominal passe de ~3 min a ~15 min (5x moins de trafic) ;
  - recul exponentiel en cas de 429, au lieu de re-taper toutes les 3 min ;
  - ARRET AUTOMATIQUE apres 24 h de limitation continue : un bot qui ne peut
    rien rapporter n'a aucune raison de continuer a aggraver la sanction ;
  - alerte de sante REPETEE (toutes les 6 h) au lieu d'un unique message
    noye dans le fil Telegram ;
  - l'alerte n'est consideree envoyee que si Telegram a reellement accepte.

Lancement :
    D:\\CrousBot\\.venv\\Scripts\\python.exe D:\\CrousBot\\bot.py
"""
from __future__ import annotations

import ctypes
import random
import sys
import time
import traceback
from pathlib import Path

from crous import fetch_annonces, BOX_94, log
from store import load_seen, save_seen, mark_seen, touch_seen
from telegram import send_message, format_annonce_telegram

# --- Reglages ---
INTERVAL_MIN = 840   # secondes (14 min)
INTERVAL_MAX = 960   # secondes (16 min)
HEADLESS = True      # False pour voir le navigateur (debug)

# Recul en cas de limitation de debit (HTTP 429).
BACKOFF_START_S = 1_800      # 30 min au premier 429
BACKOFF_MAX_S = 21_600       # plafond : 6 h
# Au-dela de cette duree de limitation CONTINUE, le bot s'arrete de lui-meme.
STOP_AFTER_RATE_LIMITED_S = 86_400   # 24 h

# Alerte de sante.
ALERT_AFTER_FAILURES = 5     # alerte apres N tours rates d'affilee
ALERT_REPEAT_S = 21_600      # ... puis rappel toutes les 6 h tant que ca dure

LOG_PATH = Path(__file__).with_name("bot.log")
LOG_MAX_BYTES = 5_000_000   # au-dela, on archive bot.log -> bot.log.old


class _Tee:
    """Ecrit a la fois sur le flux d'origine (console) et dans un fichier UTF-8.
    Fonctionne aussi sous pythonw.exe (ou le flux console est None)."""
    def __init__(self, original, fileobj):
        self.original = original
        self.fileobj = fileobj

    def write(self, data):
        if self.original is not None:
            try:
                self.original.write(data)
            except Exception:
                pass
        try:
            self.fileobj.write(data)
        except Exception:
            pass

    def flush(self):
        for s in (self.original, self.fileobj):
            try:
                if s is not None:
                    s.flush()
            except Exception:
                pass


def setup_logging() -> None:
    """Redirige stdout/stderr vers la console + bot.log (avec rotation simple)."""
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            old = LOG_PATH.with_suffix(".log.old")
            if old.exists():
                old.unlink()
            LOG_PATH.rename(old)
    except Exception:
        pass
    logfile = open(LOG_PATH, "a", encoding="utf-8", buffering=1)  # ligne par ligne
    sys.stdout = _Tee(sys.__stdout__, logfile)
    sys.stderr = _Tee(sys.__stderr__, logfile)


def une_verification(seen: dict) -> tuple[str, int]:
    """
    Fait un tour : cherche, notifie les nouveautes, met a jour la memoire.
    Renvoie (status, nb_notifiees) ou status vaut "ok", "rate_limited"
    ou "fail" (voir crous.fetch_annonces).
    """
    status, annonces = fetch_annonces(headless=HEADLESS, box=BOX_94,
                                      postal_prefix="94")
    if status != "ok":
        return status, 0

    notifiees = 0
    nb_nouvelles = 0
    for a in annonces:
        if str(a["id"]) in seen:
            touch_seen(seen, a)          # deja connue -> on rafraichit
            continue
        nb_nouvelles += 1
        # NOUVELLE annonce : on notifie AVANT de la marquer comme vue
        msg = format_annonce_telegram(a)
        if send_message(msg):
            mark_seen(seen, a)           # marquee vue seulement si envoi OK
            notifiees += 1
            log(f"  -> NOTIFIE: {a['residence']} ({a['commune']}) {a['rent_eur']} EUR")
        else:
            log(f"  -> Envoi Telegram echoue pour {a['residence']}, "
                f"on reessaiera au prochain tour.")

    save_seen(seen)
    log(f"Tour termine : {len(annonces)} dans le 94 | {nb_nouvelles} nouvelle(s) | "
        f"{notifiees} notifiee(s) | memoire = {len(seen)} id(s)")
    return "ok", notifiees


_mutex_handle = None


def ensure_single_instance() -> None:
    """Verrou global Windows : garantit qu'un seul bot tourne a la fois.
    Si une autre instance detient deja le verrou, ce doublon s'arrete net."""
    global _mutex_handle
    if not sys.platform.startswith("win"):
        return
    ERROR_ALREADY_EXISTS = 183
    handle = ctypes.windll.kernel32.CreateMutexW(
        None, False, "CrousSentinel_SingleInstance")
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        log("Une autre instance de CROUS Sentinel tourne deja -> arret du doublon.")
        sys.exit(0)
    _mutex_handle = handle  # garde le verrou vivant tant que le process vit


def _duree_lisible(secondes: float) -> str:
    """'3 h 20' / '45 min' — pour des messages Telegram comprehensibles."""
    minutes = int(secondes // 60)
    if minutes < 60:
        return f"{minutes} min"
    heures, reste = divmod(minutes, 60)
    return f"{heures} h {reste:02d}"


def main() -> None:
    setup_logging()
    ensure_single_instance()
    log("========== DEMARRAGE DE CROUS SENTINEL ==========")
    send_message("🛰️ CROUS Sentinel démarré. Surveillance du Val-de-Marne "
                 "toutes les ~15 min. Je te préviens dès qu'un logement "
                 "apparaît, et aussi si je deviens aveugle.")

    seen = load_seen()
    log(f"Memoire chargee : {len(seen)} annonce(s) deja connue(s).")

    echecs_consecutifs = 0
    derniere_alerte = 0.0          # timestamp du dernier message d'alerte ENVOYE
    backoff_s = 0.0                # recul courant en cas de 429 (0 = pas de 429)
    debut_rate_limit = 0.0         # depuis quand on est limite sans interruption

    while True:
        pause = random.randint(INTERVAL_MIN, INTERVAL_MAX)
        try:
            status, _ = une_verification(seen)

            if status == "ok":
                if echecs_consecutifs or backoff_s:
                    send_message("✅ CROUS Sentinel : accès au site rétabli, "
                                 "surveillance normale reprise.")
                echecs_consecutifs = 0
                derniere_alerte = 0.0
                backoff_s = 0.0
                debut_rate_limit = 0.0

            elif status == "rate_limited":
                echecs_consecutifs += 1
                maintenant = time.time()
                if not debut_rate_limit:
                    debut_rate_limit = maintenant
                # recul exponentiel, plafonne
                backoff_s = (BACKOFF_START_S if not backoff_s
                             else min(backoff_s * 2, BACKOFF_MAX_S))
                pause = int(backoff_s)
                duree = maintenant - debut_rate_limit
                log(f"Limitation de debit depuis {_duree_lisible(duree)}. "
                    f"Prochain essai dans {_duree_lisible(backoff_s)}.")

                # Garde-fou : on n'insiste pas indefiniment.
                if duree >= STOP_AFTER_RATE_LIMITED_S:
                    log("Limitation continue depuis plus de 24 h -> ARRET du bot.")
                    send_message(
                        "🛑 CROUS Sentinel s'arrête.\n\n"
                        f"Le site limite nos requêtes (HTTP 429) depuis "
                        f"{_duree_lisible(duree)} sans interruption. Continuer "
                        f"ne ferait qu'aggraver la sanction sur ton IP.\n\n"
                        "Utilise la 5G pour consulter le site, et relance-moi "
                        "manuellement quand l'accès sera rétabli.")
                    return

                if maintenant - derniere_alerte >= ALERT_REPEAT_S:
                    if send_message(
                            f"⚠️ CROUS Sentinel : le site limite mes requêtes "
                            f"(HTTP 429) depuis {_duree_lisible(duree)}.\n\n"
                            f"Je ne vois plus aucune annonce. J'espace mes "
                            f"essais ({_duree_lisible(backoff_s)}) et je "
                            f"m'arrêterai au bout de 24 h.\n\n"
                            f"➡️ Consulte le site en 5G en attendant."):
                        derniere_alerte = maintenant

            else:  # "fail"
                echecs_consecutifs += 1
                maintenant = time.time()
                log(f"Recherche non aboutie ({echecs_consecutifs} echec(s) "
                    f"consecutif(s)).")
                if (echecs_consecutifs >= ALERT_AFTER_FAILURES and
                        maintenant - derniere_alerte >= ALERT_REPEAT_S):
                    # L'alerte n'est "envoyee" que si Telegram l'a acceptee :
                    # sinon on reessaiera au tour suivant (bug du 17/08).
                    if send_message(
                            f"⚠️ CROUS Sentinel : {echecs_consecutifs} "
                            f"vérifications ratées d'affilée (file d'attente "
                            f"ou site injoignable).\n\n"
                            f"Je ne vois plus aucune annonce. Je continue "
                            f"d'essayer et je te relance dans 6 h si ça dure."):
                        derniere_alerte = maintenant

        except KeyboardInterrupt:
            raise
        except Exception:
            # Filet de securite ultime : rien ne doit arreter la boucle.
            log("ERREUR inattendue dans la boucle (le bot continue) :")
            traceback.print_exc()

        log(f"Prochaine verification dans {pause} s.\n")
        time.sleep(pause)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Arret demande (Ctrl+C). A bientot !")
