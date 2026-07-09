"""
Etape 3 : recherche + memoire. Affiche uniquement les NOUVELLES annonces du 94
(celles jamais vues), et met a jour la memoire locale seen.json.
Toujours pas de Telegram ici : on valide d'abord la logique "nouveautes".

Lancement :
    D:\\CrousBot\\.venv\\Scripts\\python.exe D:\\CrousBot\\check_new.py
"""
from crous import fetch_annonces_94, log
from store import load_seen, save_seen, split_new
from check_crous import format_annonce


def main() -> None:
    log("=== Recherche CROUS 94 + detection des nouveautes ===")
    ok, annonces = fetch_annonces_94(headless=True)
    if not ok:
        log("!! Recherche NON aboutie (file d'attente ou erreur). "
            "Memoire inchangee, on ne notifie rien.")
        return
    seen = load_seen()

    nouvelles, seen_maj = split_new(annonces, seen)
    save_seen(seen_maj)

    log(f"{len(annonces)} annonce(s) dans le 94 | "
        f"{len(nouvelles)} NOUVELLE(S) | "
        f"{len(annonces) - len(nouvelles)} deja connue(s) | "
        f"memoire = {len(seen_maj)} id(s)")

    if not nouvelles:
        print("\n(Aucune nouvelle annonce ce tour-ci.)")
        return

    print("\n===== NOUVELLES ANNONCES =====\n")
    for a in nouvelles:
        print(format_annonce(a))
        print()


if __name__ == "__main__":
    main()
