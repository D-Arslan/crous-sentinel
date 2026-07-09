"""
CROUS Sentinel — bot complet (boucle principale).

Boucle en continu :
  1. cherche les logements CROUS du 94 (via Playwright, gestion file d'attente),
  2. detecte les NOUVELLES annonces (memoire seen.json),
  3. envoie une notification Telegram pour chacune,
  4. attend ~2,5-3 min, puis recommence.

Concu pour tourner des jours sans s'arreter : toute erreur est loggee et le
bot continue. Arret propre avec Ctrl+C.

Lancement :
    D:\\CrousBot\\.venv\\Scripts\\python.exe D:\\CrousBot\\bot.py
"""
from __future__ import annotations

import random
import sys
import time
import traceback
from pathlib import Path

from crous import fetch_annonces_94, log
from store import load_seen, save_seen, mark_seen, touch_seen
from telegram import send_message, format_annonce_telegram

# --- Reglages ---
INTERVAL_MIN = 150   # secondes (2,5 min)
INTERVAL_MAX = 180   # secondes (3 min)
HEADLESS = True      # False pour voir le navigateur (debug)
ALERT_AFTER_FAILURES = 5   # alerte Telegram apres N tours rates d'affilee

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


def une_verification(seen: dict) -> tuple[bool, int]:
    """
    Fait un tour : cherche, notifie les nouveautes, met a jour la memoire.
    Renvoie (ok, nb_notifiees). ok=False si la recherche n'a pas abouti.
    """
    ok, annonces = fetch_annonces_94(headless=HEADLESS)
    if not ok:
        return False, 0

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
    return True, notifiees


def main() -> None:
    setup_logging()
    log("========== DEMARRAGE DE CROUS SENTINEL ==========")
    send_message("🛰️ CROUS Sentinel démarré. Surveillance du Val-de-Marne "
                 "toutes les ~3 min. Je te préviens dès qu'un logement apparaît.")

    seen = load_seen()
    log(f"Memoire chargee : {len(seen)} annonce(s) deja connue(s).")

    echecs_consecutifs = 0
    alerte_envoyee = False

    while True:
        try:
            ok, _ = une_verification(seen)
            if ok:
                echecs_consecutifs = 0
                alerte_envoyee = False
            else:
                echecs_consecutifs += 1
                log(f"Recherche non aboutie ({echecs_consecutifs} echec(s) "
                    f"consecutif(s)).")
                if echecs_consecutifs >= ALERT_AFTER_FAILURES and not alerte_envoyee:
                    send_message(
                        f"⚠️ CROUS Sentinel : {echecs_consecutifs} vérifications "
                        f"ratées d'affilée (file d'attente ou site injoignable). "
                        f"Je continue d'essayer.")
                    alerte_envoyee = True
        except KeyboardInterrupt:
            raise
        except Exception:
            # Filet de securite ultime : rien ne doit arreter la boucle.
            log("ERREUR inattendue dans la boucle (le bot continue) :")
            traceback.print_exc()

        pause = random.randint(INTERVAL_MIN, INTERVAL_MAX)
        log(f"Prochaine verification dans {pause} s.\n")
        time.sleep(pause)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Arret demande (Ctrl+C). A bientot !")
