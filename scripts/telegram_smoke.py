"""
Verification manuelle du transport Telegram : ENVOIE UN VRAI MESSAGE sur ton
Telegram pour valider que le token et le chat_id du .env sont corrects.
Ce n'est pas un test unitaire (il touche le reseau), d'ou son emplacement dans scripts/.

Utilise UNIQUEMENT la bibliotheque standard Python (urllib) : rien a installer.

Lancement :
    .venv/Scripts/python.exe scripts/telegram_smoke.py
"""

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path) -> dict:
    """Lit un fichier .env tres simple (CLE=valeur, une par ligne)."""
    if not path.exists():
        print(f"[ERREUR] Fichier introuvable : {path}")
        print("         Copie '.env.example' en '.env' et remplis-le.")
        sys.exit(1)

    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def send_message(token: str, chat_id: str, text: str) -> dict:
    """Envoie un message via l'API Telegram (HTTP POST, stdlib uniquement)."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text}
    ).encode("utf-8")

    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    env = load_env(ENV_PATH)
    token = env.get("TELEGRAM_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID", "")

    if not token or token == "colle_ton_token_ici":
        print("[ERREUR] TELEGRAM_TOKEN manquant dans .env")
        sys.exit(1)
    if not chat_id or chat_id == "colle_ton_chat_id_ici":
        print("[ERREUR] TELEGRAM_CHAT_ID manquant dans .env")
        sys.exit(1)

    print("Envoi du message de test...")
    try:
        result = send_message(token, chat_id, "🛰️ CROUS Sentinel en ligne — test OK !")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print(f"[ECHEC] Telegram a repondu {e.code} : {body}")
        print("        -> Verifie le TOKEN (BotFather) et le CHAT_ID (userinfobot),")
        print("           et que tu as bien fait 'Start' sur TON bot.")
        sys.exit(1)
    except Exception as e:
        print(f"[ECHEC] Impossible de contacter Telegram : {e}")
        print("        -> Verifie ta connexion Internet.")
        sys.exit(1)

    if result.get("ok"):
        print("[SUCCES] Message envoye ! Regarde ton Telegram. ✅")
    else:
        print(f"[ECHEC] Reponse inattendue : {result}")
        sys.exit(1)


if __name__ == "__main__":
    main()
