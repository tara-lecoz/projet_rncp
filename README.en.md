# Bennes AMP — Waste Container Removal Request Management

API for managing waste container ("benne") removal requests for the recycling centers ("déchetteries") of the Aix-Marseille Métropole (operated by Veolia). The project receives requests submitted by site staff through a Google Form, validates them, stores them, and exposes them through a tracking dashboard for the operations team.

This project is a V2: it takes over and strengthens an existing process (Google Form + manually maintained tracking sheet) by adding real-time traceability, guaranteed unique numbering, and a status that can be checked at any time.

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Project](#running-the-project)
- [API Documentation](#api-documentation)
- [Tests](#tests)
- [Project Structure](#project-structure)
- [Security](#security)
- [Known Limitations](#known-limitations)

## Overview

1. A recycling center staff member ("gardien") fills in a multi-section Google Form (Subcontracting, Métropole scheduled, Métropole one-off, Cancellation).
2. On submission, a Google Apps Script (`apps_script_updated.js`) generates a unique order number, logs the request in a tracking Google Sheet, and sends a secured JSON webhook to this API.
3. The API validates the request, stores it in the database, then notifies the operations team by email (asynchronous, non-blocking).
4. The operations team reviews and updates request status through a dashboard served directly by the API.

## Features

- Secure request intake (API key in the `X-API-Key` header)
- Strict input validation (Pydantic)
- Four request types: subcontracting, Métropole scheduled, Métropole one-off, cancellation
- Status tracking: pending, processed, cancelled — with transition rules (a cancelled request can no longer be modified)
- Search and filtering of requests (by status, request type, order number, or site name)
- Automatic email notifications (new request, cancellation) via SMTP TLS/SSL
- Real-time operations dashboard (auto-refresh, indicators, processing/cancellation actions)
- Automated test suite covering both standard and edge cases

## Architecture

```
Site staff → Google Form → Apps Script → FastAPI → Database
                                             │           │
                                             ├──► SMTP (async, notification)
                                             └──► Dashboard ←→ Operations team
```

- **Google Form**: entry point, deliberately left unchanged to stay familiar to site staff.
- **Apps Script**: generates the order number (`<site code> | <year> - <sequence>`), writes to the tracking sheet, relays the request to the API.
- **FastAPI backend**: validates, persists (SQLAlchemy), notifies (background task), exposes the data.
- **Dashboard**: HTML/Bootstrap interface served directly by the API, used by the operations team.

Each layer is independent and replaceable on its own, as long as the interface contract (the webhook JSON payload) is respected.

## Tech Stack

| Component | Technology |
|---|---|
| API | FastAPI |
| ASGI server | Uvicorn |
| ORM / Database | SQLAlchemy (SQLite in development, portable to PostgreSQL) |
| Validation | Pydantic |
| Notifications | SMTP (SSL/TLS) |
| Frontend | HTML + Bootstrap 5 |
| Form integration | Google Apps Script |
| Testing | pytest, httpx |
| Containerization | Docker |

## Prerequisites

- Python 3.11+
- An SMTP account for sending emails (e.g. Gmail with an app password)
- Docker (optional, for containerized deployment)

## Installation

```bash
git clone <repository-url>
cd projet_rncp

python -m venv .venv
.venv\Scripts\activate      # Windows
source .venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and fill in the real values:

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | Database connection string | `sqlite:///./bennes.db` |
| `API_KEY_SECRET` | Secret key expected in the webhook's `X-API-Key` header | — (required) |
| `SMTP_SERVER` | Outgoing SMTP server | `smtp.gmail.com` |
| `SMTP_PORT` | SMTP port (SSL) | `465` |
| `SMTP_USER` | Sending address for notifications | — |
| `SMTP_PASSWORD` | SMTP app password | — |
| `EMAIL_DESTINATAIRE_EXPLOITANT` | Address receiving tracking notifications | value of `SMTP_USER` |
| `CORS_ORIGINS` | Origins allowed to call the API, comma-separated | `*` |

> `API_KEY_SECRET` is required: the application refuses to start if it is not set, so that an insecure deployment never goes unnoticed. Generate a strong key with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

The `.env` file must never be committed (it is excluded via `.gitignore`).

## Running the Project

**Locally:**

```bash
uvicorn main:app --reload
```

The API is available at `http://localhost:8000`, the dashboard at `http://localhost:8000/`, and the interactive documentation (Swagger) at `http://localhost:8000/docs`.

**With Docker:**

```bash
docker build -t bennes-amp .
docker run -p 8000:8000 --env-file .env bennes-amp
```

## API Documentation

| Method | Route | Description | Authentication |
|---|---|---|---|
| `GET` | `/` | Serves the operations dashboard | — |
| `GET` | `/api/health` | API health check | — |
| `POST` | `/api/webhook/gform` | Receives a request (creation or cancellation) from Apps Script | `X-API-Key` |
| `GET` | `/api/commandes` | Lists requests, filterable by `statut`, `type_demande`, `recherche` | — |
| `PATCH` | `/api/commandes/{numero_commande}/status` | Updates a request's status (operations team) | — |

The full interactive documentation (schemas, request examples) is generated automatically by FastAPI at `/docs`.

## Tests

```bash
pytest -v
```

The `test_main.py` suite covers request creation and cancellation, rejection of unauthenticated or invalid requests, handling of duplicates and status conflicts, as well as filtering and search.

## Project Structure

```
.
├── main.py                    # FastAPI application (models, routes, business logic)
├── dashboard.html             # Operations dashboard
├── apps_script_updated.js     # Google Apps Script (webhook, numbering)
├── test_main.py               # Automated test suite
├── requirements.txt           # Python dependencies
├── Dockerfile                 # Deployment image
├── .env.example                # Configuration template
└── .gitignore
```

## Security

- Write access to the webhook protected by a dedicated API key (`X-API-Key`)
- Strict validation of all incoming data (Pydantic)
- Protection against SQL injection through exclusive use of the ORM
- Secrets isolated in `.env`, never versioned
- The dashboard is served explicitly as a single file (`FileResponse`), never by mounting the whole directory, to avoid exposing the source code or the database
- Non-privileged user inside the Docker container (no root execution)

## Known Limitations

- No formalized retention or deletion policy for the personal data collected (sender's email address), with respect to GDPR
- No `aria-live` region on the dashboard to announce the automatic refresh to screen readers
- SQLite is adequate for the current volume but is not designed for high write concurrency; a migration to PostgreSQL is planned should volume increase
