# Bennes AMP — Gestion des demandes d'enlèvement de bennes

API de gestion des demandes d'enlèvement de bennes pour les déchetteries de la Métropole Aix-Marseille (exploitées par Veolia). Le projet reçoit les demandes saisies par les gardiens de déchetterie via un Google Form, les valide, les persiste et les expose à travers un tableau de bord de suivi pour l'exploitant.

Ce projet est une V2 : il reprend et fiabilise un processus existant (Google Form + feuille de suivi manuelle) en ajoutant une traçabilité en temps réel, une numérotation garantie unique, et un suivi de statut consultable à tout moment.

## Sommaire

- [Fonctionnement général](#fonctionnement-général)
- [Fonctionnalités](#fonctionnalités)
- [Architecture](#architecture)
- [Stack technique](#stack-technique)
- [Prérequis](#prérequis)
- [Installation](#installation)
- [Configuration](#configuration)
- [Lancer le projet](#lancer-le-projet)
- [Documentation de l'API](#documentation-de-lapi)
- [Tests](#tests)
- [Structure du projet](#structure-du-projet)
- [Sécurité](#sécurité)
- [Limites connues](#limites-connues)

## Fonctionnement général

1. Un gardien de déchetterie remplit un Google Form multi-sections (Sous-traitance, Métropole prévisionnel, Métropole ponctuel, Annulation).
2. À la soumission, un script Google Apps Script (`apps_script_updated.js`) génère un numéro de commande unique, enregistre la demande dans une feuille Google Sheet de suivi, puis transmet un webhook JSON sécurisé à cette API.
3. L'API valide la demande, l'enregistre en base de données, puis notifie l'exploitant par e-mail (envoi asynchrone, non bloquant).
4. L'exploitant consulte et met à jour le statut des demandes via un tableau de bord servi directement par l'API.

## Fonctionnalités

- Réception sécurisée des demandes (clé API dans l'en-tête `X-API-Key`)
- Validation stricte des données entrantes (Pydantic)
- Quatre types de demande : sous-traitance, Métropole prévisionnel, Métropole ponctuel, annulation
- Suivi de statut : en attente, traitée, annulée — avec règles de transition (une commande annulée ne peut plus être modifiée)
- Recherche et filtrage des commandes (par statut, type de demande, numéro ou nom de déchetterie)
- Notifications e-mail automatiques (nouvelle commande, annulation) via SMTP TLS/SSL
- Tableau de bord exploitant temps réel (rafraîchissement automatique, indicateurs, actions de traitement/annulation)
- Suite de tests automatisés couvrant les cas nominaux et les cas limites

## Architecture

```
Gardien → Google Form → Apps Script → API FastAPI → Base de données
                                           │              │
                                           ├──► SMTP (async, notification)
                                           └──► Dashboard ←→ Exploitant
```

- **Google Form** : point de saisie, volontairement inchangé pour rester familier aux gardiens.
- **Apps Script** : génère le numéro de commande (`<code déchetterie> | <année> - <séquence>`), écrit dans la feuille de suivi, relaie la demande à l'API.
- **API FastAPI** : valide, persiste (SQLAlchemy), notifie (tâche de fond), expose les données.
- **Dashboard** : interface HTML/Bootstrap servie directement par l'API, consommée par l'exploitant.

Chaque couche est indépendante et remplaçable séparément, à condition de respecter le contrat d'interface (le payload JSON du webhook).

## Stack technique

| Composant | Technologie |
|---|---|
| API | FastAPI |
| Serveur ASGI | Uvicorn |
| ORM / Base de données | SQLAlchemy (SQLite en développement, portable vers PostgreSQL) |
| Validation | Pydantic |
| Notifications | SMTP (SSL/TLS) |
| Frontend | HTML + Bootstrap 5 |
| Intégration formulaire | Google Apps Script |
| Tests | pytest, httpx |
| Conteneurisation | Docker |

## Prérequis

- Python 3.11+
- Un compte SMTP pour l'envoi d'e-mails (ex. Gmail avec mot de passe d'application)
- Docker (optionnel, pour un déploiement conteneurisé)

## Installation

```bash
git clone <url-du-dépôt>
cd projet_rncp

python -m venv .venv
.venv\Scripts\activate      # Windows
source .venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
```

## Configuration

Copier `.env.example` en `.env` et renseigner les valeurs réelles :

| Variable | Description | Défaut |
|---|---|---|
| `DATABASE_URL` | Chaîne de connexion à la base de données | `sqlite:///./bennes.db` |
| `API_KEY_SECRET` | Clé secrète attendue dans l'en-tête `X-API-Key` du webhook | — (obligatoire) |
| `SMTP_SERVER` | Serveur SMTP sortant | `smtp.gmail.com` |
| `SMTP_PORT` | Port SMTP (SSL) | `465` |
| `SMTP_USER` | Adresse d'envoi des notifications | — |
| `SMTP_PASSWORD` | Mot de passe d'application SMTP | — |
| `EMAIL_DESTINATAIRE_EXPLOITANT` | Adresse recevant les notifications de suivi | valeur de `SMTP_USER` |
| `CORS_ORIGINS` | Origines autorisées à appeler l'API, séparées par des virgules | `*` |

> `API_KEY_SECRET` est obligatoire : l'application refuse de démarrer si elle n'est pas définie, pour éviter tout déploiement non sécurisé passé inaperçu. Générer une clé forte avec `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

Le fichier `.env` ne doit jamais être commité (il est exclu via `.gitignore`).

## Lancer le projet

**En local :**

```bash
uvicorn main:app --reload
```

L'API est disponible sur `http://localhost:8000`, le tableau de bord sur `http://localhost:8000/`, et la documentation interactive (Swagger) sur `http://localhost:8000/docs`.

**Avec Docker :**

```bash
docker build -t bennes-amp .
docker run -p 8000:8000 --env-file .env bennes-amp
```

## Documentation de l'API

| Méthode | Route | Description | Authentification |
|---|---|---|---|
| `GET` | `/` | Sert le tableau de bord exploitant | — |
| `GET` | `/api/health` | Vérification de l'état de l'API | — |
| `POST` | `/api/webhook/gform` | Réception d'une demande (création ou annulation) depuis Apps Script | `X-API-Key` |
| `GET` | `/api/commandes` | Liste des commandes, filtrable par `statut`, `type_demande`, `recherche` | — |
| `PATCH` | `/api/commandes/{numero_commande}/status` | Mise à jour du statut d'une commande par l'exploitant | — |

La documentation interactive complète (schémas, exemples de requêtes) est générée automatiquement par FastAPI sur `/docs`.

## Tests

```bash
pytest -v
```

La suite `test_main.py` couvre la création et l'annulation de commandes, le rejet des requêtes non authentifiées ou invalides, la gestion des doublons et des conflits de statut, ainsi que le filtrage et la recherche.

## Structure du projet

```
.
├── main.py                    # API FastAPI (modèles, routes, logique métier)
├── dashboard.html             # Tableau de bord exploitant
├── apps_script_updated.js     # Script Google Apps Script (webhook, numérotation)
├── test_main.py               # Suite de tests automatisés
├── requirements.txt           # Dépendances Python
├── Dockerfile                 # Image de déploiement
├── .env.example                # Modèle de configuration
└── .gitignore
```

## Sécurité

- Accès en écriture au webhook protégé par une clé API dédiée (`X-API-Key`)
- Validation stricte de toutes les données entrantes (Pydantic)
- Protection contre l'injection SQL par usage exclusif de l'ORM
- Secrets isolés dans `.env`, jamais versionnés
- Le tableau de bord est servi explicitement par fichier (`FileResponse`), jamais par montage du répertoire complet, pour ne pas exposer le code source ou la base de données
- Utilisateur non privilégié dans le conteneur Docker (pas d'exécution en root)

## Limites connues

- Pas de politique de conservation ou de suppression formalisée pour les données personnelles collectées (adresse e-mail de l'expéditeur), au regard du RGPD
- Pas de région `aria-live` sur le tableau de bord pour signaler le rafraîchissement automatique aux lecteurs d'écran
- SQLite convient au volume actuel mais n'est pas conçu pour une forte concurrence d'écriture ; une migration vers PostgreSQL est prévue si le volume évolue
