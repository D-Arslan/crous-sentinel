"""
Suite de tests de CROUS Sentinel — AUCUN acces reseau.

Valide la chaine complete contexte -> recherche -> parsing -> filtre ->
dedoublonnage -> memoire -> formatage du message, en rejouant des fixtures
JSON. C'est ce qui manquait au projet : jusqu'ici les tests validaient des
bouts de logique sur des donnees injectees a la main, mais personne n'avait
verifie que la chaine entiere detectait reellement un logement.

Stdlib uniquement (unittest), fidele au principe zero-dependance du projet.

Lancement :
    .venv/Scripts/python.exe -m unittest discover -s tests -t . -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import crous
from crous import (parse_context, _postal_and_city, parse_item, matches_zone,
                   collect, replay_annonces, build_payload,
                   _raise_if_rate_limited, RateLimitedError, PAGE_SIZE)
from store import split_new, mark_seen, load_seen, save_seen
from telegram import format_annonce_telegram

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


class TestContexte(unittest.TestCase):
    """Lecture des campagnes actives."""

    def test_garde_les_campagnes_activees(self):
        tools = parse_context(json.loads(
            (FIXTURES / "context.json").read_text(encoding="utf-8")))
        self.assertEqual([t["id"] for t in tools], [42, 47])

    def test_ignore_les_campagnes_desactivees(self):
        """La campagne 38 (isEnabled=False) ne doit pas remonter."""
        tools = parse_context(json.loads(
            (FIXTURES / "context.json").read_text(encoding="utf-8")))
        self.assertNotIn(38, [t["id"] for t in tools])

    def test_contexte_vide_ne_plante_pas(self):
        for data in ({}, {"tools": None}, None):
            self.assertEqual(parse_context(data), [])


class TestParsingAdresse(unittest.TestCase):
    """Extraction code postal + commune."""

    def test_villes_temoins(self):
        cas = [
            ("3 rue Camille Claudel, 94400 Vitry-sur-Seine", "94400", "Vitry-sur-Seine"),
            ("5 rue Alsace-Lorraine, 40000 Mont-de-Marsan", "40000", "Mont-de-Marsan"),
            ("Quartier Porette, 20250 Corte", "20250", "Corte"),
            ("8 av. Gambetta, 24000 Perigueux", "24000", "Perigueux"),
        ]
        for adresse, cp, ville in cas:
            with self.subTest(adresse=adresse):
                self.assertEqual(_postal_and_city(adresse), (cp, ville))

    def test_adresse_sans_code_postal(self):
        self.assertEqual(_postal_and_city("lieu-dit Les Sables"), (None, None))
        self.assertEqual(_postal_and_city(""), (None, None))
        self.assertEqual(_postal_and_city(None), (None, None))


class TestParsingItem(unittest.TestCase):
    """Conversion item brut -> annonce."""

    def setUp(self):
        self.item = {
            "id": 2001, "label": "T1",
            "residence": {"label": "Residence des Landes",
                          "address": "5 rue Alsace-Lorraine, 40000 Mont-de-Marsan"},
            "occupationModes": [{"rent": {"min": 27350}}, {"rent": {"min": 31000}}],
            "area": {"min": 20}, "available": True, "lowStock": True,
        }

    def test_loyer_converti_de_centimes_en_euros(self):
        """Piege connu de l'API : les loyers sont en CENTIMES."""
        self.assertEqual(parse_item(self.item, 47)["rent_eur"], 273.50)

    def test_retient_le_loyer_minimum(self):
        """Deux modes d'occupation -> on annonce le moins cher."""
        self.assertEqual(parse_item(self.item, 47)["rent_eur"], 273.50)

    def test_url_annonce(self):
        a = parse_item(self.item, 47)
        self.assertEqual(
            a["url"],
            "https://trouverunlogement.lescrous.fr/tools/47/accommodations/2001")

    def test_sans_loyer_ne_plante_pas(self):
        self.assertIsNone(parse_item({"id": 1}, 42)["rent_eur"])

    def test_commune_inconnue(self):
        a = parse_item({"id": 1, "residence": {"address": "nulle part"}}, 42)
        self.assertEqual(a["commune"], "?")


class TestFiltreZone(unittest.TestCase):
    def test_filtre_94(self):
        self.assertTrue(matches_zone({"postal": "94400"}, "94"))
        self.assertTrue(matches_zone({"postal": "94800"}, "94"))
        self.assertFalse(matches_zone({"postal": "92230"}, "94"))
        self.assertFalse(matches_zone({"postal": None}, "94"))

    def test_sans_filtre_tout_passe(self):
        self.assertTrue(matches_zone({"postal": "40000"}, None))
        self.assertTrue(matches_zone({"postal": None}, None))


class TestDedoublonnage(unittest.TestCase):
    def test_meme_id_compte_une_fois(self):
        items = [{"id": 7, "residence": {"address": "1 rue X, 94400 Vitry"}},
                 {"id": 7, "residence": {"address": "1 rue X, 94400 Vitry"}}]
        annonces = {}
        collect(items, 42, "94", annonces)
        self.assertEqual(len(annonces), 1)


class TestChaineComplete(unittest.TestCase):
    """LE test qui manquait : la chaine detecte-t-elle vraiment un logement ?"""

    def test_zone_94_retient_les_bons_logements(self):
        status, annonces = replay_annonces(FIXTURES, postal_prefix="94")
        self.assertEqual(status, "ok")
        ids = sorted(a["id"] for a in annonces)
        # 1001/1002/1003 (campagne 42) + 2004 (campagne 47), 1001 dedoublonne
        self.assertEqual(ids, [1001, 1002, 1003, 2004])

    def test_hors_zone_exclu(self):
        _, annonces = replay_annonces(FIXTURES, postal_prefix="94")
        postaux = {a["postal"] for a in annonces}
        self.assertTrue(all(p.startswith("94") for p in postaux))
        self.assertNotIn("92230", postaux)
        self.assertNotIn("75011", postaux)

    def test_sans_filtre_les_villes_temoins_remontent(self):
        """Mont-de-Marsan, Corte et Perigueux : disponibilites constatees
        manuellement le 16/09/2026. La chaine doit savoir les detecter."""
        status, annonces = replay_annonces(FIXTURES, postal_prefix=None)
        self.assertEqual(status, "ok")
        postaux = {a["postal"] for a in annonces}
        for cp in ("40000", "20250", "24000"):
            with self.subTest(code_postal=cp):
                self.assertIn(cp, postaux)

    def test_total_sans_filtre(self):
        _, annonces = replay_annonces(FIXTURES, postal_prefix=None)
        self.assertEqual(len(annonces), 10)   # 5 + 6 items, 1 doublon

    def test_pagination_250_logements(self):
        """Regression : l'ancienne version s'arretait aux 100 premiers."""
        status, annonces = replay_annonces(FIXTURES / "pagination",
                                           postal_prefix="94")
        self.assertEqual(status, "ok")
        self.assertEqual(len(annonces), 250)
        self.assertGreater(len(annonces), PAGE_SIZE)

    def test_fixtures_absentes(self):
        with tempfile.TemporaryDirectory() as d:
            status, annonces = replay_annonces(Path(d))
            self.assertEqual(status, "fail")
            self.assertEqual(annonces, [])


class TestDonneesReelles(unittest.TestCase):
    """Rejeu sur les donnees REELLES capturees le 18/09/2026 (export HAR du
    navigateur, campagne 47). Ignore si les fixtures ne sont pas presentes."""

    @classmethod
    def setUpClass(cls):
        cls.dossier = FIXTURES / "real"
        if not (cls.dossier / "context.json").exists():
            raise unittest.SkipTest("fixtures reelles absentes")

    def test_villes_temoins_au_centime_pres(self):
        """Prix verifies contre une capture d'ecran du site."""
        _, annonces = replay_annonces(self.dossier, postal_prefix=None)
        attendus = {
            "24000": ("PATIO", 323.31),
            "40000": ("LE VELUM", 403.91),
            "20250": ("Residence Pierrette Grimaldi", 237.00),
        }
        for cp, (residence, prix) in attendus.items():
            with self.subTest(code_postal=cp):
                trouve = [a for a in annonces
                          if a["postal"] == cp and a["residence"] == residence
                          and a["rent_eur"] == prix]
                self.assertTrue(trouve, f"{residence} a {prix} EUR introuvable")

    def test_val_de_marne_vide(self):
        """LA reponse a la question ouverte depuis juillet 2026 : le 94 est
        reellement vide. seen.json vide = penurie, pas bug."""
        _, annonces = replay_annonces(self.dossier, postal_prefix="94")
        self.assertEqual(annonces, [])

    def test_couverture_nationale(self):
        _, annonces = replay_annonces(self.dossier, postal_prefix=None)
        self.assertGreater(len(annonces), 10)
        departements = {(a["postal"] or "?")[:2] for a in annonces}
        self.assertGreater(len(departements), 5)


class TestLimitationDebit(unittest.TestCase):
    def test_429_leve_une_exception(self):
        with self.assertRaises(RateLimitedError):
            _raise_if_rate_limited(429)

    def test_autres_statuts_passent(self):
        for s in (200, 404, 500, None):
            _raise_if_rate_limited(s)   # ne doit rien lever


class TestPayload(unittest.TestCase):
    def test_pagination_dans_le_payload(self):
        p = build_payload(47, "residual", crous.BOX_94, page_no=3)
        self.assertEqual(p["page"], 3)
        self.assertEqual(p["pageSize"], PAGE_SIZE)
        self.assertEqual(p["idTool"], 47)
        self.assertEqual(p["toolMechanism"], "residual")
        self.assertEqual(p["location"], crous.BOX_94)


class TestMemoire(unittest.TestCase):
    """seen.json : ne notifier que les nouveautes."""

    def test_nouvelles_puis_connues(self):
        _, annonces = replay_annonces(FIXTURES, postal_prefix="94")
        nouvelles, memoire = split_new(annonces, {})
        self.assertEqual(len(nouvelles), 4)

        # Deuxieme passage : plus rien de neuf.
        seen = {}
        for a in annonces:
            mark_seen(seen, a)
        nouvelles2, _ = split_new(annonces, seen)
        self.assertEqual(nouvelles2, [])

    def test_ecriture_relecture(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "seen.json"
            seen = {}
            mark_seen(seen, {"id": 42, "residence": "R", "commune": "Vitry",
                             "postal": "94400", "rent_eur": 300.0, "url": "u"})
            save_seen(seen, p)
            self.assertEqual(load_seen(p).keys(), seen.keys())

    def test_fichier_corrompu_repart_a_vide(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "seen.json"
            p.write_text("{ceci n'est pas du json", encoding="utf-8")
            self.assertEqual(load_seen(p), {})


class TestMessageTelegram(unittest.TestCase):
    def test_prix_format_francais(self):
        _, annonces = replay_annonces(FIXTURES, postal_prefix=None)
        landes = next(a for a in annonces if a["postal"] == "40000")
        msg = format_annonce_telegram(landes)
        self.assertIn("273,50", msg)          # virgule, pas point
        self.assertIn("Mont-de-Marsan", msg)
        self.assertIn("accommodations/2001", msg)

    def test_marqueurs_stock(self):
        _, annonces = replay_annonces(FIXTURES, postal_prefix="94")
        claudel = next(a for a in annonces if a["id"] == 1001)
        self.assertIn("stock faible", format_annonce_telegram(claudel))


if __name__ == "__main__":
    unittest.main(verbosity=2)
