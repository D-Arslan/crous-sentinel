@echo off
REM Lance le bot CROUS 94 en continu.
REM Double-clique ce fichier, ou lance-le depuis un terminal.
cd /d "%~dp0"
".venv\Scripts\python.exe" "bot.py"
echo.
echo Le bot s'est arrete. Appuie sur une touche pour fermer.
pause >nul
