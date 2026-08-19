/**
 * Apps Script à coller dans l'éditeur du Google Form (Extensions > Apps Script)
 * gérant les demandes d'enlèvement de bennes en déchetterie - Métropole Aix-Marseille.
 *
 * Le formulaire comporte 4 sections (branchement par page selon la réponse à la
 * question "Type de demande") :
 *   - Sous-traitance
 *   - Métropole prévisionnel
 *   - Métropole ponctuel
 *   - Annulation
 *
 * Rôle du script :
 *   1. À la soumission, extraire les réponses de la section active.
 *   2. Pour une création (3 premiers types) : générer le N° de commande
 *      (format "042 | 2026 - 000017"), l'écrire dans la Google Sheet liée,
 *      puis notifier le déposant par e-mail.
 *   3. Pour une annulation : récupérer le N° de commande à annuler saisi par
 *      l'agent.
 *   4. Dans tous les cas : transmettre un webhook JSON sécurisé (X-API-Key)
 *      à l'API FastAPI.
 *
 * Installation :
 *   1. Ouvrir le Google Form -> Extensions -> Apps Script.
 *   2. Coller ce script, l'enregistrer.
 *   3. Renseigner les Script Properties (Project Settings > Script Properties) :
 *        API_URL      -> ex: http://localhost:8000/api/webhook/gform
 *        API_KEY      -> doit correspondre à API_KEY_SECRET du fichier .env
 *        ADMIN_EMAIL  -> adresse recevant les alertes d'échec technique
 *   4. Déclencheurs (Triggers) -> Ajouter un déclencheur :
 *        Fonction "onFormSubmit", Événement "Sur envoi du formulaire".
 */

// -----------------------------------------------------------------------
// Configuration
// -----------------------------------------------------------------------

// Code numérique (3 chiffres) attribué à chaque déchetterie pour la
// génération du N° de commande. À compléter selon le périmètre réel.
const CODES_DECHETTERIE = {
  "Déchetterie d'Aix-en-Provence": "001",
  "Déchetterie de Marseille": "002",
  "Déchetterie de Vitrolles": "003",
  "Déchetterie de Septèmes": "004",
  "Déchetterie de Marignane": "005",
};

const CODE_DECHETTERIE_PAR_DEFAUT = "000";

const TITRE_QUESTION_TYPE_DEMANDE = "Type de demande";

// Libellés de section (page du formulaire) -> valeur envoyée à l'API.
const MAPPING_TYPE_DEMANDE = {
  "Sous-traitance": "SOUS_TRAITANCE",
  "Métropole prévisionnel": "METROPOLE_PREVISIONNEL",
  "Métropole ponctuel": "METROPOLE_PONCTUEL",
  "Annulation": "ANNULATION",
};

// Correspondance entre le titre de la question du Google Form et le champ
// attendu par l'API, pour les sections de création de commande.
const MAPPING_CHAMPS_CREATION = {
  "Nom de la déchetterie": "dechetterie_nom",
  "Déchetterie": "dechetterie_nom",
  "Commentaire": "commentaire",
  "Observations": "commentaire",
};

// Titres de question exclus du regroupement automatique dans "details_matieres"
// (déjà traités explicitement par ailleurs).
const CHAMPS_EXCLUS_DES_MATIERES = [
  TITRE_QUESTION_TYPE_DEMANDE,
  "Nom de la déchetterie",
  "Déchetterie",
  "Commentaire",
  "Observations",
  "Adresse e-mail",
  "N° de commande à annuler",
];

const TITRE_QUESTION_NUMERO_A_ANNULER = "N° de commande à annuler";

// -----------------------------------------------------------------------
// Point d'entrée
// -----------------------------------------------------------------------

function onFormSubmit(e) {
  try {
    const itemReponses = e.response.getItemResponses();
    const typeDemandeBrut = trouverReponseParTitre(itemReponses, TITRE_QUESTION_TYPE_DEMANDE);
    const typeDemande = MAPPING_TYPE_DEMANDE[typeDemandeBrut];

    if (!typeDemande) {
      throw new Error('Type de demande non reconnu : "' + typeDemandeBrut + '"');
    }

    const payload =
      typeDemande === "ANNULATION"
        ? construirePayloadAnnulation(e, itemReponses)
        : construirePayloadCreation(e, itemReponses, typeDemande);

    envoyerWebhookFastAPI(payload);

    if (typeDemande !== "ANNULATION") {
      enregistrerDansSheet(payload);
    }

    envoyerEmailSecurise(payload, typeDemande);
  } catch (erreur) {
    console.error("Erreur onFormSubmit : " + erreur.message);
    notifierErreurAdmin(erreur);
  }
}

// -----------------------------------------------------------------------
// Construction des payloads
// -----------------------------------------------------------------------

function construirePayloadCreation(e, itemReponses, typeDemande) {
  const donnees = {
    dechetterie_nom: "",
    email_expediteur: e.response.getRespondentEmail() || "",
    type_demande: typeDemande,
    details_matieres: "",
    commentaire: "",
  };

  const matieresRenseignees = [];

  itemReponses.forEach(function (itemReponse) {
    const titre = itemReponse.getItem().getTitle().trim();
    const reponse = itemReponse.getResponse();
    const valeur = Array.isArray(reponse) ? reponse.join(", ") : String(reponse);

    if (!valeur || !valeur.trim()) return;

    const champCible = MAPPING_CHAMPS_CREATION[titre];
    if (champCible) {
      donnees[champCible] = valeur;
      return;
    }

    if (CHAMPS_EXCLUS_DES_MATIERES.indexOf(titre) === -1) {
      matieresRenseignees.push(titre + " : " + valeur);
    }
  });

  if (matieresRenseignees.length > 0) {
    donnees.details_matieres = matieresRenseignees.join(" | ");
  }
  if (!donnees.dechetterie_nom) {
    donnees.dechetterie_nom = "Non renseignée";
  }

  const codeDechetterie = CODES_DECHETTERIE[donnees.dechetterie_nom] || CODE_DECHETTERIE_PAR_DEFAUT;
  donnees.numero_commande = genererNumeroCommande(codeDechetterie);

  return donnees;
}

function construirePayloadAnnulation(e, itemReponses) {
  const numeroAAnnuler = trouverReponseParTitre(itemReponses, TITRE_QUESTION_NUMERO_A_ANNULER);
  if (!numeroAAnnuler) {
    throw new Error("Aucun N° de commande à annuler n'a été fourni.");
  }

  return {
    numero_commande: numeroAAnnuler.trim(),
    type_demande: "ANNULATION",
    email_expediteur: e.response.getRespondentEmail() || "",
  };
}

function trouverReponseParTitre(itemReponses, titreRecherche) {
  for (let i = 0; i < itemReponses.length; i++) {
    const titre = itemReponses[i].getItem().getTitle().trim();
    if (titre === titreRecherche) {
      const reponse = itemReponses[i].getResponse();
      return Array.isArray(reponse) ? reponse.join(", ") : String(reponse);
    }
  }
  return null;
}

// -----------------------------------------------------------------------
// Génération du N° de commande : "<code déchetterie> | <année> - <séquence>"
// Compteur persistant par déchetterie et par année via PropertiesService.
// -----------------------------------------------------------------------

function genererNumeroCommande(codeDechetterie) {
  const proprietes = PropertiesService.getScriptProperties();
  const annee = new Date().getFullYear();
  const cleCompteur = "COMPTEUR_" + codeDechetterie + "_" + annee;

  const verrou = LockService.getScriptLock();
  verrou.waitLock(10000);

  try {
    const compteurActuel = parseInt(proprietes.getProperty(cleCompteur) || "0", 10);
    const nouveauCompteur = compteurActuel + 1;
    proprietes.setProperty(cleCompteur, String(nouveauCompteur));

    const sequenceFormatee = String(nouveauCompteur).padStart(6, "0");
    return codeDechetterie + " | " + annee + " - " + sequenceFormatee;
  } finally {
    verrou.releaseLock();
  }
}

// -----------------------------------------------------------------------
// Écriture dans le Google Sheet lié au formulaire
// -----------------------------------------------------------------------

function enregistrerDansSheet(payload) {
  const feuille = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("Suivi commandes")
    || SpreadsheetApp.getActiveSpreadsheet().insertSheet("Suivi commandes");

  if (feuille.getLastRow() === 0) {
    feuille.appendRow([
      "N° commande",
      "Date",
      "Déchetterie",
      "Type de demande",
      "Détail matières",
      "Commentaire",
      "E-mail expéditeur",
      "Statut",
    ]);
  }

  feuille.appendRow([
    payload.numero_commande,
    new Date(),
    payload.dechetterie_nom,
    payload.type_demande,
    payload.details_matieres,
    payload.commentaire,
    payload.email_expediteur,
    "EN_ATTENTE",
  ]);
}

// -----------------------------------------------------------------------
// Webhook vers l'API FastAPI
// -----------------------------------------------------------------------

function envoyerWebhookFastAPI(payload) {
  const proprietes = PropertiesService.getScriptProperties();
  const apiUrl = proprietes.getProperty("API_URL") || "http://localhost:8000/api/webhook/gform";
  const apiKey = proprietes.getProperty("API_KEY");

  if (!apiKey) {
    throw new Error("API_KEY manquante dans les Script Properties.");
  }

  const options = {
    method: "post",
    contentType: "application/json",
    headers: {
      "X-API-Key": apiKey,
    },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
  };

  const reponse = UrlFetchApp.fetch(apiUrl, options);
  const code = reponse.getResponseCode();

  if (code < 200 || code >= 300) {
    throw new Error(
      "Échec de l'envoi à l'API (HTTP " + code + ") : " + reponse.getContentText()
    );
  }
}

// -----------------------------------------------------------------------
// Notifications e-mail
// -----------------------------------------------------------------------

function envoyerEmailSecurise(payload, typeDemande) {
  if (!payload.email_expediteur) return;

  const estAnnulation = typeDemande === "ANNULATION";
  const sujet = estAnnulation
    ? "[Bennes AMP] Confirmation d'annulation - " + payload.numero_commande
    : "[Bennes AMP] Confirmation de votre demande - " + payload.numero_commande;

  const corps = estAnnulation
    ? "Votre demande d'annulation pour la commande " + payload.numero_commande + " a bien été prise en compte."
    : "Votre demande d'enlèvement a bien été enregistrée.\n\n" +
      "N° de commande : " + payload.numero_commande + "\n" +
      "Déchetterie : " + payload.dechetterie_nom + "\n" +
      "Type de demande : " + payload.type_demande + "\n" +
      "Merci de conserver ce numéro pour toute annulation ultérieure.";

  try {
    MailApp.sendEmail(payload.email_expediteur, sujet, corps);
  } catch (erreur) {
    console.error("Échec de l'envoi de l'e-mail de confirmation : " + erreur.message);
  }
}

function notifierErreurAdmin(erreur) {
  const adminEmail = PropertiesService.getScriptProperties().getProperty("ADMIN_EMAIL");
  if (!adminEmail) return;

  MailApp.sendEmail(
    adminEmail,
    "[Bennes AMP] Échec de traitement d'une soumission Google Form",
    "Le script Apps Script n'a pas pu traiter une soumission de formulaire.\n\n" +
      "Détail de l'erreur : " + erreur.message
  );
}
