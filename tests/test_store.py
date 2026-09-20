"""
Tests de la memoire (store.py) et d'un cycle complet de bot.py avec le
transport Telegram simule. AUCUN acces reseau, AUCUNE ecriture dans le
seen.json du projet.

Lancement :
    .venv/Scripts/python.exe -m unittest discover -s tests -t . -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bot
from store import load_seen, save_seen, mark_seen, touch_seen, split_new


def _annonce(id_: int, residence: str = "Residence Test") -> dict:
    return {"id": id_, "id_tool": 47, "residence": residence, "type": "T1",
            "address": "1 rue de la Gare, 94400 Vitry-sur-Seine",
            "postal": "94400", "commune": "Vitry-sur-Seine", "rent_eur": 350.0,
            "area_min": 18, "available": True, "low_stock": False,
            "high_demand": False, "url": "https://example.invalid/47/%d" % id_}


class TestLoadSeen(unittest.TestCase):
    def test_fichier_absent(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(load_seen(Path(d) / "absent.json"), {})

    def test_contenu_non_dictionnaire(self):
        """Une liste ou un scalaire valide en JSON n'est pas une memoire."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "seen.json"
            for contenu in ("[1, 2]", "42", "null"):
                p.write_text(contenu, encoding="utf-8")
                self.assertEqual(load_seen(p), {})

    def test_fichier_corrompu(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "seen.json"
            p.write_bytes(b"{\"1001\": {\"residence\": \"tronq")
            self.assertEqual(load_seen(p), {})


class TestSaveSeen(unittest.TestCase):
    def test_ecriture_puis_relecture(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "seen.json"
            data = {"1001": {"residence": "A", "rent_eur": 300.5}}
            save_seen(data, p)
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), data)

    def test_pas_de_fichier_temporaire_residuel(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "seen.json"
            save_seen({"1": {}}, p)
            self.assertFalse(p.with_suffix(".json.tmp").exists())

    def test_echec_ecriture_ne_plante_pas(self):
        """Dossier inexistant : on logge, on ne leve rien."""
        p = Path(tempfile.gettempdir()) / "crous_sentinel_inexistant" / "x" / "seen.json"
        save_seen({"1": {}}, p)   # ne doit pas lever
        self.assertFalse(p.exists())

    def test_ecrasement_atomique_conserve_l_ancien_contenu_si_echec(self):
        """Le fichier precedent reste intact tant que le remplacement n'a pas eu lieu."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "seen.json"
            save_seen({"ancien": {}}, p)
            with mock.patch("store.os.replace", side_effect=OSError("disque plein")):
                save_seen({"nouveau": {}}, p)
            self.assertEqual(load_seen(p), {"ancien": {}})
            self.assertFalse(p.with_suffix(".json.tmp").exists())


class TestMarqueurs(unittest.TestCase):
    def test_mark_seen_puis_touch_seen(self):
        seen = {}
        mark_seen(seen, _annonce(1001))
        self.assertIn("1001", seen)
        self.assertEqual(seen["1001"]["first_seen"], seen["1001"]["last_seen"])
        seen["1001"]["last_seen"] = "2000-01-01 00:00:00"
        touch_seen(seen, _annonce(1001))
        self.assertNotEqual(seen["1001"]["last_seen"], "2000-01-01 00:00:00")

    def test_touch_seen_ignore_les_inconnues(self):
        seen = {}
        touch_seen(seen, _annonce(1))
        self.assertEqual(seen, {})

    def test_split_new_ne_modifie_pas_l_entree(self):
        seen = {"1": {"residence": "A", "last_seen": "x"}}
        nouvelles, maj = split_new([_annonce(1), _annonce(2)], seen)
        self.assertEqual([a["id"] for a in nouvelles], [2])
        self.assertEqual(set(maj), {"1", "2"})
        self.assertEqual(seen, {"1": {"residence": "A", "last_seen": "x"}})


class TestCycle(unittest.TestCase):
    """Un tour de bot.une_verification, avec recherche, Telegram et
    sauvegarde remplaces par des doubles. Le seen.json du projet n'est
    jamais touche."""

    def setUp(self):
        self.saves = []
        patches = [
            mock.patch("bot.save_seen", side_effect=lambda s: self.saves.append(dict(s))),
            mock.patch("bot.log"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_nouvelle_annonce_notifiee_puis_memorisee(self):
        seen = {}
        with mock.patch("bot.fetch_annonces", return_value=("ok", [_annonce(1001)])), \
             mock.patch("bot.send_message", return_value=True) as send:
            status, n = bot.une_verification(seen)
        self.assertEqual((status, n), ("ok", 1))
        self.assertIn("1001", seen)
        self.assertEqual(send.call_count, 1)
        self.assertIn("Residence Test", send.call_args.args[0])
        self.assertEqual(len(self.saves), 1)

    def test_envoi_echoue_annonce_non_memorisee(self):
        """Telegram en panne : l'annonce reste 'nouvelle' pour le tour suivant."""
        seen = {}
        with mock.patch("bot.fetch_annonces", return_value=("ok", [_annonce(1001)])), \
             mock.patch("bot.send_message", return_value=False):
            status, n = bot.une_verification(seen)
        self.assertEqual((status, n), ("ok", 0))
        self.assertEqual(seen, {})

    def test_annonce_connue_pas_de_doublon(self):
        seen = {}
        mark_seen(seen, _annonce(1001))
        with mock.patch("bot.fetch_annonces", return_value=("ok", [_annonce(1001)])), \
             mock.patch("bot.send_message", return_value=True) as send:
            status, n = bot.une_verification(seen)
        self.assertEqual((status, n), ("ok", 0))
        send.assert_not_called()

    def test_echec_de_recherche_ne_touche_a_rien(self):
        """Un echec n'est jamais interprete comme '0 logement'."""
        seen = {}
        for statut in ("fail", "rate_limited"):
            with mock.patch("bot.fetch_annonces", return_value=(statut, [])), \
                 mock.patch("bot.send_message") as send:
                status, n = bot.une_verification(seen)
            self.assertEqual((status, n), (statut, 0))
            send.assert_not_called()
        self.assertEqual(self.saves, [])


if __name__ == "__main__":
    unittest.main()
