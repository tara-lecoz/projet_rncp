"""
Tests unitaires pour l'API Gestion des Bennes (V2).

Couvre :
- Réception du webhook Google Form avec clé API valide (création de commande)
- Rejet du webhook avec clé API invalide (401)
- Annulation d'une commande référencée via le webhook (type_demande=ANNULATION)
- Récupération de la liste des commandes sur /api/commandes (avec filtres)
- Modification du statut d'une commande par l'exploitant
"""

import os
import tempfile

import pytest

# Les variables d'environnement doivent être présentes AVANT l'import de "main",
# car main.py les lit au chargement du module (engine SQLAlchemy, clé API...).
_TEST_DB_FD, _TEST_DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_TEST_DB_FD)

os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"
os.environ["API_KEY_SECRET"] = "test-secret-key"
os.environ.setdefault("SMTP_USER", "")
os.environ.setdefault("SMTP_PASSWORD", "")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

client = TestClient(main.app)

HEADERS_VALIDES = {"X-API-Key": "test-secret-key"}
HEADERS_INVALIDES = {"X-API-Key": "mauvaise-cle"}


@pytest.fixture(autouse=True)
def base_vide():
    """Vide la table des commandes avant chaque test pour garantir l'isolation."""
    db = main.SessionLocal()
    db.query(main.Commande).delete()
    db.commit()
    db.close()
    yield


def payload_creation(**overrides):
    base = {
        "numero_commande": "001 | 2026 - 000001",
        "dechetterie_nom": "Déchetterie de Septèmes",
        "email_expediteur": "gardien@ampmetropole.fr",
        "type_demande": "METROPOLE_PONCTUEL",
        "details_matieres": "Bois : palettes et planches",
        "commentaire": "Benne pleine à 90%",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Webhook Google Form - création
# ---------------------------------------------------------------------------

def test_webhook_gform_cree_une_commande_avec_cle_api_valide():
    reponse = client.post(
        "/api/webhook/gform", json=payload_creation(), headers=HEADERS_VALIDES
    )
    assert reponse.status_code == 201
    corps = reponse.json()
    assert corps["numero_commande"] == "001 | 2026 - 000001"
    assert corps["dechetterie_nom"] == "Déchetterie de Septèmes"
    assert corps["type_demande"] == "METROPOLE_PONCTUEL"
    assert corps["statut"] == "EN_ATTENTE"


def test_webhook_gform_refuse_cle_api_invalide():
    reponse = client.post(
        "/api/webhook/gform", json=payload_creation(), headers=HEADERS_INVALIDES
    )
    assert reponse.status_code == 401


def test_webhook_gform_refuse_cle_api_absente():
    reponse = client.post("/api/webhook/gform", json=payload_creation())
    assert reponse.status_code in (401, 422)


def test_webhook_gform_refuse_payload_invalide():
    payload_incomplet = {"type_demande": "METROPOLE_PONCTUEL"}  # numero_commande manquant
    reponse = client.post(
        "/api/webhook/gform", json=payload_incomplet, headers=HEADERS_VALIDES
    )
    assert reponse.status_code == 422


def test_webhook_gform_refuse_numero_commande_deja_existant():
    client.post("/api/webhook/gform", json=payload_creation(), headers=HEADERS_VALIDES)
    reponse = client.post(
        "/api/webhook/gform", json=payload_creation(), headers=HEADERS_VALIDES
    )
    assert reponse.status_code == 409


# ---------------------------------------------------------------------------
# Webhook Google Form - annulation référencée
# ---------------------------------------------------------------------------

def test_webhook_gform_annule_une_commande_referencee():
    client.post("/api/webhook/gform", json=payload_creation(), headers=HEADERS_VALIDES)

    payload_annulation = {
        "numero_commande": "001 | 2026 - 000001",
        "type_demande": "ANNULATION",
        "email_expediteur": "gardien@ampmetropole.fr",
    }
    reponse = client.post(
        "/api/webhook/gform", json=payload_annulation, headers=HEADERS_VALIDES
    )
    assert reponse.status_code == 200
    assert reponse.json()["statut"] == "ANNULEE"


def test_webhook_gform_annulation_commande_inexistante():
    payload_annulation = {
        "numero_commande": "999 | 2026 - 999999",
        "type_demande": "ANNULATION",
    }
    reponse = client.post(
        "/api/webhook/gform", json=payload_annulation, headers=HEADERS_VALIDES
    )
    assert reponse.status_code == 404


# ---------------------------------------------------------------------------
# Listing des commandes
# ---------------------------------------------------------------------------

def test_lister_commandes_retourne_les_commandes_creees():
    client.post("/api/webhook/gform", json=payload_creation(), headers=HEADERS_VALIDES)
    client.post(
        "/api/webhook/gform",
        json=payload_creation(
            numero_commande="002 | 2026 - 000001",
            dechetterie_nom="Déchetterie de Vitrolles",
            type_demande="SOUS_TRAITANCE",
        ),
        headers=HEADERS_VALIDES,
    )

    reponse = client.get("/api/commandes")
    assert reponse.status_code == 200
    assert len(reponse.json()) == 2


def test_lister_commandes_filtre_par_statut():
    client.post("/api/webhook/gform", json=payload_creation(), headers=HEADERS_VALIDES)
    client.patch(
        "/api/commandes/001 | 2026 - 000001/status", json={"statut": "TRAITEE"}
    )

    reponse_en_attente = client.get("/api/commandes", params={"statut": "EN_ATTENTE"})
    reponse_traitees = client.get("/api/commandes", params={"statut": "TRAITEE"})

    assert reponse_en_attente.json() == []
    assert len(reponse_traitees.json()) == 1


def test_lister_commandes_recherche_par_numero_ou_dechetterie():
    client.post("/api/webhook/gform", json=payload_creation(), headers=HEADERS_VALIDES)

    reponse = client.get("/api/commandes", params={"recherche": "Septèmes"})
    assert len(reponse.json()) == 1

    reponse_numero = client.get("/api/commandes", params={"recherche": "000001"})
    assert len(reponse_numero.json()) == 1


def test_lister_commandes_recherche_par_email():
    client.post("/api/webhook/gform", json=payload_creation(), headers=HEADERS_VALIDES)

    reponse = client.get("/api/commandes", params={"recherche": "gardien@ampmetropole.fr"})
    assert len(reponse.json()) == 1


# ---------------------------------------------------------------------------
# Modification du statut par l'exploitant
# ---------------------------------------------------------------------------

def test_modifier_statut_vers_traitee():
    client.post("/api/webhook/gform", json=payload_creation(), headers=HEADERS_VALIDES)

    reponse = client.patch(
        "/api/commandes/001 | 2026 - 000001/status", json={"statut": "TRAITEE"}
    )
    assert reponse.status_code == 200
    assert reponse.json()["statut"] == "TRAITEE"


def test_modifier_statut_vers_annulee():
    client.post("/api/webhook/gform", json=payload_creation(), headers=HEADERS_VALIDES)

    reponse = client.patch(
        "/api/commandes/001 | 2026 - 000001/status", json={"statut": "ANNULEE"}
    )
    assert reponse.status_code == 200
    assert reponse.json()["statut"] == "ANNULEE"


def test_modifier_statut_commande_inexistante():
    reponse = client.patch(
        "/api/commandes/999 | 2026 - 999999/status", json={"statut": "TRAITEE"}
    )
    assert reponse.status_code == 404


def test_modifier_statut_commande_deja_annulee_est_refuse():
    client.post("/api/webhook/gform", json=payload_creation(), headers=HEADERS_VALIDES)
    client.patch("/api/commandes/001 | 2026 - 000001/status", json={"statut": "ANNULEE"})

    reponse = client.patch(
        "/api/commandes/001 | 2026 - 000001/status", json={"statut": "TRAITEE"}
    )
    assert reponse.status_code == 409


# ---------------------------------------------------------------------------
# Tableau de bord
# ---------------------------------------------------------------------------

def test_dashboard_est_servi_a_la_racine():
    reponse = client.get("/")
    assert reponse.status_code == 200
    assert "text/html" in reponse.headers["content-type"]
