# CROUS Sentinel — Bot d'alerte logement CROUS (Val-de-Marne)

> Bot Telegram qui surveille en continu le site officiel du CROUS
> (`trouverunlogement.lescrous.fr`) et **notifie en temps réel** dès qu'un
> logement étudiant se libère dans le Val-de-Marne (94). Conçu avec une
> **priorité absolue sur la fiabilité** : ne rate rien, pas de doublon, ne
> crashe jamais.

---

## 1. Le problème

Les logements CROUS sont **très demandés** et **partent en quelques minutes**.
Le site officiel a deux difficultés majeures :

1. Une **file d'attente** (« Vous êtes trop nombreux ! ») qui bloque l'accès
   quand le trafic est élevé.
2. Une **protection anti-bot** : un simple appel HTTP (script naïf, `requests`,
   `curl`) reçoit systématiquement la page de file d'attente au lieu du contenu.

Surveiller le site à la main est impossible (il faudrait rafraîchir jour et nuit).
D'où ce bot : il fait le guet à ma place et m'envoie une notification Telegram
avec **le nom de la résidence, le prix et le lien direct** dès qu'une annonce
apparaît.

---

## 2. La solution en un coup d'œil

```
┌──────────────┐   toutes les ~3 min    ┌────────────────────────┐
│    bot.py    │ ─────────────────────► │  Playwright (Chromium) │
│ (boucle)     │                        │  passe la file d'attente│
└──────┬───────┘                        └───────────┬────────────┘
       │                                            │ appelle l'API interne
       │                                            ▼
       │                               POST /api/fr/search/{idTool}
       │                                            │  (JSON structuré)
       │        ┌───────────────┐                   ▼
       │        │  store.py     │◄──── filtre 94xxx + parsing
       │        │  seen.json    │      (nom, prix, commune, lien)
       │        └───────┬───────┘
       │  nouveautés    │
       ▼  uniquement    ▼
┌──────────────┐   ┌────────────────┐
│ telegram.py  │──►│  📱 Telegram   │
└──────────────┘   └────────────────┘
```

**Idée-clé** : plutôt que de « scraper » le HTML (fragile, casse au moindre
changement de design), le bot pilote un **vrai navigateur** pour franchir la
file d'attente, puis appelle l'**API JSON interne** du site depuis ce navigateur.
On obtient des données propres et structurées, avec la robustesse d'un navigateur.

---

## 3. Stack technique

| Élément | Choix | Pourquoi |
|---|---|---|
| Langage | **Python 3.12** | Simple, écosystème riche |
| Navigateur automatisé | **Playwright** (Chromium) | Franchit file d'attente + anti-bot |
| Notifications | **Telegram Bot API** via `urllib` (stdlib) | Zéro dépendance, ultra-fiable |
| Stockage mémoire | **Fichier JSON** local (`seen.json`) | Simple, suffisant, écriture atomique |
| Exécution continue | **Planificateur de tâches Windows** | Démarrage auto + relance auto |
| Environnement | **venv** local | Dépendances isolées |

Seule dépendance externe : `playwright`. Tout le reste est en bibliothèque
standard.

---

## 4. Comment ça marche en détail

### 4.1 Franchir la file d'attente
Le site renvoie une page au titre « Vous êtes trop nombreux ! » quand il est
saturé. La fonction `load_through_queue()` (dans `crous.py`) :
1. charge la page,
2. détecte la file d'attente (recherche du texte dans le `<title>`),
3. si détectée : attend 20 s puis réessaie (jusqu'à 10 fois),
4. sinon : on est passé, on continue.

### 4.2 Trouver les logements via l'API interne
Une fois le navigateur passé, on lit `/api/global/context` pour connaître les
**campagnes actives** (`idTool`), puis on interroge pour chacune :

```
POST /api/fr/search/{idTool}
{
  "idTool": 47,
  "location": [{"lon": 2.30, "lat": 48.87}, {"lon": 2.63, "lat": 48.64}],
  "price": {"max": 10000000},
  "pageSize": 100,
  ...
}
```

- `location` est une **bounding box** (2 coins d'un rectangle géographique) qui
  couvre tout le Val-de-Marne.
- On interroge **toutes** les campagnes actives (année courante + année suivante)
  et on **dédoublonne** par `id` de logement.

### 4.3 Filtrer précisément le 94
La bounding box peut légèrement déborder sur Paris (75), la Seine-Saint-Denis
(93) ou l'Essonne (91). On garde donc uniquement les annonces dont le **code
postal commence par `94`** (extrait de l'adresse). Le **nom exact de la commune**
est lu directement dans l'adresse (`… 94800 Villejuif` → « Villejuif ») :
toutes les communes du 94 sont couvertes et correctement nommées.

### 4.4 Ne notifier que les nouveautés
`store.py` gère un fichier `seen.json` (dictionnaire indexé par `id` de logement).
À chaque tour, une annonce dont l'`id` n'est pas déjà en mémoire est
« nouvelle » → on la notifie. Détails de fiabilité :
- **Écriture atomique** (`fichier.tmp` puis `os.replace`) : jamais de fichier
  corrompu, même si le PC s'éteint pendant l'écriture.
- Fichier absent/corrompu → on repart d'une mémoire vide sans planter.

### 4.5 Notifier sur Telegram
`telegram.py` envoie le message via l'API Telegram (`urllib`, 3 tentatives avec
backoff). Une annonce est marquée « vue » **seulement après un envoi réussi** :
si Telegram est momentanément injoignable, l'annonce sera **réessayée** au tour
suivant → aucune annonce perdue.

### 4.6 La boucle principale
`bot.py` orchestre tout, en continu :
```
démarrage → message Telegram "démarré"
répéter indéfiniment :
    (ok, annonces) = recherche 94
    si échec (file d'attente infranchissable...) :
        compter l'échec ; alerter après 5 échecs consécutifs
    sinon :
        pour chaque nouvelle annonce : notifier, puis mémoriser si envoi OK
    attendre 150–180 s (aléa) puis recommencer
```

---

## 5. Garanties de fiabilité (le cœur du projet)

| Garantie | Comment |
|---|---|
| **Ne crashe jamais** | Chaque tour est encapsulé dans un `try/except` ; toute erreur est loggée et la boucle continue |
| **Ne rate aucune annonce** | Marquée « vue » uniquement après notification réussie (sinon réessai) |
| **Aucun doublon** | Mémoire persistante indexée par `id`, écriture atomique |
| **Survit à la file d'attente** | Navigateur réel + détection/attente/réessai |
| **Distingue « 0 logement » de « échec »** | La recherche renvoie un statut `ok`, jamais un silence trompeur |
| **Prévient si le bot devient aveugle** | Alerte Telegram après 5 échecs consécutifs |
| **Logs consultables** | Journal dans `bot.log` (même sans fenêtre), rotation à 5 Mo |

Ces garanties ont été **validées par des tests déterministes** (simulation de
plusieurs tours, panne Telegram, fichier corrompu) : 12/12 vérifications OK.

---

## 6. Structure du projet

```
D:\CrousBot\
├── bot.py               # Boucle principale (orchestration + logs)
├── crous.py             # Recherche CROUS (file d'attente, API, filtre 94)
├── store.py             # Mémoire des annonces vues (seen.json)
├── telegram.py          # Envoi des notifications Telegram
├── check_crous.py       # Outil manuel : liste les logements du 94 (étape 2)
├── check_new.py         # Outil manuel : liste seulement les nouveautés (étape 3)
├── test_telegram.py     # Outil manuel : message de test Telegram (étape 1)
├── start_bot.bat        # Lanceur manuel (double-clic)
├── install_task.ps1     # Installe le démarrage automatique (Windows)
├── uninstall_task.ps1   # Retire le démarrage automatique
├── .env                 # SECRET : token Telegram + chat_id (non versionné)
├── .env.example         # Modèle de configuration
├── seen.json            # Mémoire (généré automatiquement)
├── bot.log              # Journal (généré automatiquement)
├── README.md            # Guide d'utilisation
├── documentation.md     # Ce document (FR)
└── documentation_en.md  # Documentation complète (EN)
```

### Rôle de chaque module
- **`crous.py`** : `load_through_queue()`, `get_active_tools()`, `search_tool()`,
  `parse_item()`, `is_in_94()`, `fetch_annonces_94()` → renvoie `(ok, annonces)`.
- **`store.py`** : `load_seen()`, `save_seen()` (atomique), `mark_seen()`,
  `touch_seen()`, `split_new()`.
- **`telegram.py`** : `load_env()`, `send_message()` (avec réessais),
  `format_annonce_telegram()`.
- **`bot.py`** : `setup_logging()` (logs console + fichier), `une_verification()`,
  `main()` (boucle).

---

## 7. Installation (repartir de zéro)

```powershell
# 1. Créer l'environnement virtuel
cd D:\CrousBot
python -m venv .venv

# 2. Installer Playwright + le navigateur Chromium
.\.venv\Scripts\python.exe -m pip install playwright
.\.venv\Scripts\python.exe -m playwright install chromium

# 3. Configurer les secrets : copier .env.example en .env et remplir
#    TELEGRAM_TOKEN=...   (obtenu via @BotFather sur Telegram)
#    TELEGRAM_CHAT_ID=... (ton identifiant de chat)
```

### Créer le bot Telegram (rappel)
1. Sur Telegram, parler à **@BotFather** → `/newbot` → suivre les instructions →
   récupérer le **token**.
2. Démarrer une conversation avec ton nouveau bot (bouton **Start**).
3. Récupérer ton **chat_id** (ex. via @userinfobot, ou l'API `getUpdates`).

---

## 8. Utilisation — toutes les commandes

### Lancer manuellement (fenêtre visible, logs en direct)
```powershell
& "D:\CrousBot\.venv\Scripts\python.exe" "D:\CrousBot\bot.py"
```
ou double-clic sur **`start_bot.bat`**. Arrêt : **Ctrl + C**.

### Activer le démarrage automatique (une seule fois)
```powershell
powershell -ExecutionPolicy Bypass -File "D:\CrousBot\install_task.ps1"
```
Crée une tâche planifiée `CrousSentinel` qui :
- démarre à l'ouverture de session,
- se relance automatiquement en cas de plantage,
- tourne **sans fenêtre** (via `pythonw.exe`),
- écrit ses logs dans `bot.log`.

### Gérer la tâche
```powershell
# Voir les logs en direct
Get-Content D:\CrousBot\bot.log -Wait -Tail 20

# Vérifier l'état
Get-ScheduledTask -TaskName CrousSentinel

# Pause temporaire (réactivable facilement)
Disable-ScheduledTask -TaskName CrousSentinel
Stop-ScheduledTask    -TaskName CrousSentinel   # coupe aussi le bot en cours
Enable-ScheduledTask  -TaskName CrousSentinel   # réactive

# Suppression complète
powershell -ExecutionPolicy Bypass -File "D:\CrousBot\uninstall_task.ps1"
```

### Outils de diagnostic
```powershell
# Tester uniquement Telegram
& "D:\CrousBot\.venv\Scripts\python.exe" "D:\CrousBot\test_telegram.py"

# Afficher tous les logements du 94 (sans notifier)
& "D:\CrousBot\.venv\Scripts\python.exe" "D:\CrousBot\check_crous.py"

# Afficher seulement les nouveautés (et mettre à jour la mémoire)
& "D:\CrousBot\.venv\Scripts\python.exe" "D:\CrousBot\check_new.py"
```

---

## 9. Configuration / personnalisation

### Dans `bot.py`
| Réglage | Défaut | Rôle |
|---|---|---|
| `INTERVAL_MIN` / `INTERVAL_MAX` | 150 / 180 s | Intervalle entre 2 vérifications (avec aléa) |
| `HEADLESS` | `True` | `False` pour voir le navigateur (debug) |
| `ALERT_AFTER_FAILURES` | 5 | Nb d'échecs consécutifs avant alerte Telegram |
| `LOG_MAX_BYTES` | 5 Mo | Taille max de `bot.log` avant rotation |

### Dans `crous.py`
| Réglage | Rôle |
|---|---|
| `BOX_94` | Rectangle géographique couvrant le 94 |
| `COMMUNES_94` | Libellés de secours (le nom est surtout lu dans l'adresse) |

**Changer de département** : remplacer `BOX_94` par la bounding box voulue et
adapter le filtre de code postal dans `is_in_94()` / `_postal_and_city()`.

---

## 10. Détails techniques notables (pour la culture / la démo)

- **La file d'attente ne touche que les clients « simples »** : un `curl` reçoit
  la page « trop nombreux », mais un navigateur Playwright passe. C'est ce qui
  justifie tout le choix d'architecture.
- **`idTool` est dynamique** : chaque campagne CROUS (année courante, année
  suivante…) a un identifiant qui change dans le temps. On le lit à chaud via
  `/api/global/context` plutôt que de le coder en dur → le bot survit aux
  changements de campagne.
- **Le loyer est renvoyé en centimes** dans l'API (`29893` → `298,93 €`).
- **Lien direct d'une annonce** : `/tools/{idTool}/accommodations/{id}`.
- **Encodage Windows** : la console Windows (cp1252) ne sait pas afficher les
  emoji → les logs console restent en texte simple, les emoji sont réservés aux
  messages Telegram (envoyés en UTF-8 via HTTP). Les logs fichier sont en UTF-8.

---

## 11. Limites connues & pistes d'évolution

**Limites actuelles :**
- Tourne quand la session Windows est **ouverte** (PC allumé et connecté).
- Ne couvre pas le cas « PC éteint / en veille ».

**Évolutions possibles (roadmap portfolio) :**
- ☁️ **Déploiement cloud** (petit VPS / Raspberry Pi) → surveillance 24h/24
  indépendante du PC personnel.
- 🔁 **Re-notification** si un logement disparaît puis réapparaît (créneau qui
  se rouvre).
- 🎛️ **Filtres avancés** : prix max, type (studio / T1 / colocation), surface min.
- 📊 **Historique & statistiques** : heures/jours où les annonces tombent le plus.
- 🐳 **Conteneurisation Docker** pour un déploiement reproductible.
- 🤖 **Commandes Telegram interactives** (`/status`, `/pause`, `/filtre 400`).

---

## 12. Ce que ce projet démontre (compétences)

- **Reverse-engineering** d'une application web moderne (SvelteKit) : inspection
  du trafic réseau, découverte et réutilisation d'une API interne non documentée.
- **Automatisation navigateur** (Playwright) et contournement d'une file
  d'attente / protection anti-bot.
- **Ingénierie de la fiabilité** : gestion d'erreurs exhaustive, écriture
  atomique, idempotence (pas de doublon), reprise sur panne, distinction
  échec / résultat vide.
- **Tests déterministes** d'une logique dépendant du réseau (par simulation).
- **Déploiement / ops** : service always-on via Planificateur de tâches Windows,
  logs avec rotation, exécution sans fenêtre.
- **Conception incrémentale** : construit et validé par étapes vérifiables
  (Telegram → recherche → mémoire → boucle complète → always-on).

---

*Projet personnel — développé avec l'assistance de Claude Code.*
