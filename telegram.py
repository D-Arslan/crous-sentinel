"""
Envoi de messages Telegram (etape 4).
Base sur urllib (bibliotheque standard) : aucune dependance, tres fiable.
Lit le token et le chat_id depuis le fichier .env.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

ENV_PATH = Path(__file__).with_name(".env")
API = "https://api.telegram.org/bot{token}/sendMessage"


def load_env(path: Path = ENV_PATH) -> dict:
    values = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        values[k.strip()] = v.strip().strip('"').strip("'")
    return values


_ENV = load_env()
TOKEN = _ENV.get("TELEGRAM_TOKEN", "")
CHAT_ID = _ENV.get("TELEGRAM_CHAT_ID", "")


def send_message(text: str, retries: int = 3, disable_preview: bool = True) -> bool:
    """
    Envoie un message Telegram. Renvoie True si succes, False sinon.
    Reessaie 'retries' fois en cas d'erreur reseau. Ne plante jamais.
    """
    if not TOKEN or not CHAT_ID:
        print("[telegram] TOKEN ou CHAT_ID manquant dans .env")
        return False

    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": "true" if disable_preview else "false",
    }).encode("utf-8")

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                API.format(token=TOKEN), data=data, method="POST")
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("ok"):
                return True
            print(f"[telegram] Reponse KO: {payload}")
        except Exception as e:
            print(f"[telegram] Envoi echoue (essai {attempt}/{retries}): {e}")
        time.sleep(3 * attempt)  # backoff simple
    return False


def format_annonce_telegram(a: dict) -> str:
    """Message lisible pour une annonce (le lien est auto-clique par Telegram)."""
    prix = (f"{a['rent_eur']:.2f}".replace(".", ",") + " €/mois"
            if a.get("rent_eur") is not None else "prix ?")
    surface = f"{a['area_min']:.0f} m²" if a.get("area_min") else "surface ?"
    flags = []
    if a.get("low_stock"):
        flags.append("⚠️ stock faible")
    if a.get("high_demand"):
        flags.append("🔥 très demandé")
    flag_line = ("\n" + " · ".join(flags)) if flags else ""
    return (
        f"🏠 NOUVEAU logement CROUS — {a.get('commune', '94')}\n\n"
        f"🏢 {a.get('residence', '?')}\n"
        f"📍 {a.get('address', '?')}\n"
        f"🛏️ {a.get('type', '?')} · {surface}\n"
        f"💶 {prix}{flag_line}\n\n"
        f"🔗 {a.get('url', '')}"
    )


if __name__ == "__main__":
    ok = send_message("🤖 Test module telegram.py — OK")
    print("Envoye:", ok)
