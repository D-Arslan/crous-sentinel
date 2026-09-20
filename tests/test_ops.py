"""
Tests d'exploitation : rotation du journal par cycle (bot.py) et politique
de reessai Telegram (telegram.py). Aucun reseau, aucun fichier du projet.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bot
import telegram


class TestRotationJournal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "bot.log"
        self.old = self.path.with_suffix(".log.old")

    def test_pas_de_rotation_sous_le_seuil(self):
        f = bot._open_log(self.path)
        self.addCleanup(f.close)
        f.write("petit\n")
        f.flush()
        self.assertIs(bot._rotate_file(self.path, f, max_bytes=100), f)
        self.assertFalse(self.old.exists())

    def test_ferme_renomme_rouvre_et_ecrase_l_ancien_old(self):
        self.old.write_text("ancien .old a ecraser", encoding="utf-8")
        f = bot._open_log(self.path)
        f.write("x" * 200 + "\n")
        f.flush()

        nouveau = bot._rotate_file(self.path, f, max_bytes=100)
        self.addCleanup(nouveau.close)

        self.assertTrue(f.closed)                           # ferme
        self.assertIsNot(nouveau, f)                        # rouvert
        self.assertEqual(self.old.read_text(encoding="utf-8"),
                         "x" * 200 + "\n")                  # renomme, .old ecrase
        nouveau.write("apres rotation\n")
        nouveau.flush()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "apres rotation\n")

    def test_rotate_log_if_needed_rebranche_les_tee(self):
        f = bot._open_log(self.path)
        f.write("y" * 200 + "\n")
        f.flush()
        tee = bot._Tee(None, f)
        with mock.patch.object(bot, "LOG_PATH", self.path), \
             mock.patch.object(bot, "LOG_MAX_BYTES", 100), \
             mock.patch.object(bot, "_logfile", f), \
             mock.patch.object(bot.sys, "stdout", tee), \
             mock.patch.object(bot.sys, "stderr", tee):
            bot.rotate_log_if_needed()
            self.addCleanup(bot._logfile.close)
            self.assertIs(tee.fileobj, bot._logfile)
            self.assertIsNot(tee.fileobj, f)
            tee.write("ligne\n")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "ligne\n")
        self.assertTrue(self.old.exists())


class TestReessaiTelegram(unittest.TestCase):
    def test_trois_echecs_deux_attentes(self):
        with mock.patch.object(telegram, "TOKEN", "t"), \
             mock.patch.object(telegram, "CHAT_ID", "c"), \
             mock.patch.object(telegram.urllib.request, "urlopen",
                               side_effect=OSError("reseau")), \
             mock.patch.object(telegram.time, "sleep") as sleep:
            self.assertFalse(telegram.send_message("x", retries=3))
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4])

    def test_sans_identifiants_pas_d_appel_reseau(self):
        with mock.patch.object(telegram, "TOKEN", ""), \
             mock.patch.object(telegram.urllib.request, "urlopen") as urlopen:
            self.assertFalse(telegram.send_message("x"))
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
