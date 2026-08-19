"""
API FastAPI - Gestion des bennes pour les déchetteries de la Métropole Aix-Marseille (V2).

Architecture :
- Les gardiens n'ont pas d'accès direct à l'application : ils saisissent leurs
  demandes via un Google Form multi-sections (Sous-traitance, Métropole
  prévisionnel, Métropole ponctuel, Annulation).
- Le script Google Apps Script (apps_script_updated.js) génère le N° de
  commande et transmet un webhook JSON sécurisé à cette API.
- Persistance SQLite via SQLAlchemy (ORM -> protection anti-injection SQL).
- Validation stricte des payloads via Pydantic.
- Notifications e-mail asynchrones (BackgroundTasks) via SMTP TLS/SSL.
- Tableau de bord exploitant servi directement par l'API (dashboard.html).
"""

from __future__ import annotations

import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.text import MIMEText
from enum import Enum
from typing import Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine, or_
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration (secrets isolés dans .env)
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./bennes.db")
API_KEY_SECRET = os.getenv("API_KEY_SECRET")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAIL_DESTINATAIRE_EXPLOITANT = os.getenv("EMAIL_DESTINATAIRE_EXPLOITANT", SMTP_USER)
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

if not API_KEY_SECRET:
    raise RuntimeError(
        "API_KEY_SECRET manquant : définissez-le dans le fichier .env "
        "(voir .env.example)."
    )

# ---------------------------------------------------------------------------
# Base de données
# ---------------------------------------------------------------------------

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class TypeDemande(str, Enum):
    SOUS_TRAITANCE = "SOUS_TRAITANCE"
    METROPOLE_PREVISIONNEL = "METROPOLE_PREVISIONNEL"
    METROPOLE_PONCTUEL = "METROPOLE_PONCTUEL"
    ANNULATION = "ANNULATION"


class StatutCommande(str, Enum):
    EN_ATTENTE = "EN_ATTENTE"
    TRAITEE = "TRAITEE"
    ANNULEE = "ANNULEE"


class Commande(Base):
    """Une demande d'enlèvement de benne saisie par un gardien via le Google Form."""

    __tablename__ = "commandes"

    id = Column(Integer, primary_key=True, index=True)
    numero_commande = Column(String(50), unique=True, nullable=False, index=True)
    dechetterie_nom = Column(String(120), nullable=False, index=True)
    email_expediteur = Column(String(255), nullable=True)
    type_demande = Column(String(30), nullable=False)
    details_matieres = Column(Text, nullable=True)
    commentaire = Column(Text, nullable=True)
    statut = Column(String(20), nullable=False, default=StatutCommande.EN_ATTENTE.value)
    date_demande = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    date_modification = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Schémas Pydantic (validation stricte des entrées)
# ---------------------------------------------------------------------------

class GFormWebhookPayload(BaseModel):
    """Payload envoyé par le script Apps Script à la soumission du Google Form.

    - Pour les types SOUS_TRAITANCE / METROPOLE_PREVISIONNEL / METROPOLE_PONCTUEL :
      "numero_commande" est le nouveau N° généré par Apps Script.
    - Pour le type ANNULATION : "numero_commande" référence la commande
      existante à annuler.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    numero_commande: str = Field(..., min_length=1, max_length=50)
    type_demande: TypeDemande
    dechetterie_nom: Optional[str] = Field(None, max_length=120)
    email_expediteur: Optional[EmailStr] = None
    details_matieres: Optional[str] = None
    commentaire: Optional[str] = None


class CommandeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    numero_commande: str
    dechetterie_nom: str
    email_expediteur: Optional[str]
    type_demande: str
    details_matieres: Optional[str]
    commentaire: Optional[str]
    statut: str
    date_demande: datetime
    date_modification: datetime


class StatutUpdateIn(BaseModel):
    statut: StatutCommande


# ---------------------------------------------------------------------------
# Sécurité : clé API pour le webhook
# ---------------------------------------------------------------------------

def verify_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> None:
    if x_api_key != API_KEY_SECRET:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clé API invalide.",
        )


# ---------------------------------------------------------------------------
# Service d'envoi de mail (asynchrone, TLS/SSL)
# ---------------------------------------------------------------------------

def _envoyer_mail(destinataire: str, sujet: str, corps: str) -> None:
    """Envoie un e-mail via SMTP SSL. Échoue silencieusement en log si mal configuré,
    pour ne jamais bloquer la logique métier (BackgroundTasks best-effort)."""
    if not SMTP_USER or not SMTP_PASSWORD or not destinataire:
        print(f"[mail] SMTP non configuré - envoi ignoré ({sujet})")
        return

    message = MIMEText(corps, "plain", "utf-8")
    message["Subject"] = sujet
    message["From"] = SMTP_USER
    message["To"] = destinataire

    try:
        contexte = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=contexte) as serveur:
            serveur.login(SMTP_USER, SMTP_PASSWORD)
            serveur.sendmail(SMTP_USER, [destinataire], message.as_string())
    except Exception as exc:  # noqa: BLE001 - notification best-effort, ne doit pas casser l'API
        print(f"[mail] Échec de l'envoi ({sujet}) : {exc}")


def notifier_nouvelle_commande(commande: Commande) -> None:
    sujet = f"[Bennes AMP] Nouvelle demande {commande.numero_commande} - {commande.dechetterie_nom}"
    corps = (
        f"Une nouvelle demande d'enlèvement a été enregistrée.\n\n"
        f"N° commande : {commande.numero_commande}\n"
        f"Déchetterie : {commande.dechetterie_nom}\n"
        f"Type de demande : {commande.type_demande}\n"
        f"Détail matières : {commande.details_matieres or '-'}\n"
        f"Commentaire : {commande.commentaire or '-'}\n"
        f"Date : {commande.date_demande.isoformat()}\n"
    )
    _envoyer_mail(EMAIL_DESTINATAIRE_EXPLOITANT, sujet, corps)


def notifier_annulation(commande: Commande) -> None:
    sujet = f"[Bennes AMP] Annulation de la demande {commande.numero_commande}"
    corps = (
        f"La demande suivante a été annulée.\n\n"
        f"N° commande : {commande.numero_commande}\n"
        f"Déchetterie : {commande.dechetterie_nom}\n"
        f"Type de demande : {commande.type_demande}\n"
    )
    _envoyer_mail(EMAIL_DESTINATAIRE_EXPLOITANT, sujet, corps)
    if commande.email_expediteur:
        _envoyer_mail(
            commande.email_expediteur,
            sujet,
            "Votre demande d'enlèvement a bien été annulée.\n\n" + corps,
        )


# ---------------------------------------------------------------------------
# Application FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
    title="API Gestion des Bennes - Métropole Aix-Marseille",
    description="Réception des demandes gardiens (Google Form), suivi et annulation.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
def servir_dashboard():
    """Sert le tableau de bord exploitant. Un montage StaticFiles sur la racine du
    projet exposerait main.py/.env/bennes.db : on sert donc explicitement ce seul
    fichier plutôt que de monter tout le répertoire."""
    return FileResponse("dashboard.html")


@app.get("/api/health", tags=["monitoring"])
def health_check():
    return {"status": "ok", "date": datetime.now(timezone.utc).isoformat()}


@app.post(
    "/api/webhook/gform",
    response_model=CommandeOut,
    tags=["webhook"],
    dependencies=[Depends(verify_api_key)],
)
def recevoir_webhook_gform(
    payload: GFormWebhookPayload,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Reçoit les données du Google Form (via Apps Script).

    - type_demande == ANNULATION : met à jour la commande référencée par
      "numero_commande" au statut ANNULEE et notifie par e-mail.
    - Autres types : crée une nouvelle commande au statut EN_ATTENTE.
    """
    if payload.type_demande == TypeDemande.ANNULATION:
        commande = (
            db.query(Commande)
            .filter(Commande.numero_commande == payload.numero_commande)
            .first()
        )
        if commande is None:
            raise HTTPException(status_code=404, detail="Commande référencée introuvable.")
        if commande.statut == StatutCommande.ANNULEE.value:
            raise HTTPException(status_code=409, detail="Cette commande est déjà annulée.")

        commande.statut = StatutCommande.ANNULEE.value
        commande.date_modification = datetime.now(timezone.utc)
        db.commit()
        db.refresh(commande)

        background_tasks.add_task(notifier_annulation, commande)
        response.status_code = status.HTTP_200_OK
        return commande

    existante = (
        db.query(Commande)
        .filter(Commande.numero_commande == payload.numero_commande)
        .first()
    )
    if existante is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Le N° de commande {payload.numero_commande} existe déjà.",
        )

    commande = Commande(
        numero_commande=payload.numero_commande,
        dechetterie_nom=payload.dechetterie_nom or "Non renseignée",
        email_expediteur=payload.email_expediteur,
        type_demande=payload.type_demande.value,
        details_matieres=payload.details_matieres,
        commentaire=payload.commentaire,
        statut=StatutCommande.EN_ATTENTE.value,
    )
    db.add(commande)
    db.commit()
    db.refresh(commande)

    background_tasks.add_task(notifier_nouvelle_commande, commande)
    response.status_code = status.HTTP_201_CREATED
    return commande


@app.get("/api/commandes", response_model=list[CommandeOut], tags=["commandes"])
def lister_commandes(
    statut: Optional[StatutCommande] = None,
    type_demande: Optional[TypeDemande] = None,
    recherche: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Liste des commandes pour le tableau de bord exploitant.

    "recherche" filtre sur le N° de commande ou le nom de la déchetterie
    (recherche partielle, insensible à la casse).
    """
    query = db.query(Commande)
    if statut is not None:
        query = query.filter(Commande.statut == statut.value)
    if type_demande is not None:
        query = query.filter(Commande.type_demande == type_demande.value)
    if recherche:
        motif = f"%{recherche.strip()}%"
        query = query.filter(
            or_(
                Commande.numero_commande.ilike(motif),
                Commande.dechetterie_nom.ilike(motif),
            )
        )
    return query.order_by(Commande.date_demande.desc()).all()


@app.patch(
    "/api/commandes/{numero_commande}/status",
    response_model=CommandeOut,
    tags=["commandes"],
)
def modifier_statut_commande(
    numero_commande: str,
    payload: StatutUpdateIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Permet à l'exploitant de faire évoluer le statut d'une commande."""
    commande = (
        db.query(Commande).filter(Commande.numero_commande == numero_commande).first()
    )
    if commande is None:
        raise HTTPException(status_code=404, detail="Commande introuvable.")
    if commande.statut == StatutCommande.ANNULEE.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Impossible de modifier une commande annulée.",
        )

    commande.statut = payload.statut.value
    commande.date_modification = datetime.now(timezone.utc)
    db.commit()
    db.refresh(commande)

    if commande.statut == StatutCommande.ANNULEE.value:
        background_tasks.add_task(notifier_annulation, commande)

    return commande


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
