# RGPD : comment TalentFlow traite les données des candidats

Un CV contient des données personnelles (art. 4 RGPD). Un outil de staffing doit donc
démontrer sa conformité dès la conception (art. 25, *privacy by design*). Ce document
résume les choix faits et ce qui reste à valider avec le DPO de l'entreprise.

## 1. Minimisation : le LLM ne voit jamais l'identité

| Donnée | Traitement | Pourquoi |
|---|---|---|
| Nom, prénom, email, téléphone, adresse, URL LinkedIn/GitHub, IBAN, n° de sécurité sociale | **remplacés** par des jetons (`[PERSONNE_1]`, `[EMAIL_1]`…) | inutiles au matching ; nécessaires plus tard pour contacter le consultant |
| Date de naissance, âge, nationalité, situation familiale, titre de séjour | **supprimés** | inutiles au matching et sources de discrimination (art. L1132-1 du Code du travail) |
| Nom du fichier (« CV_Prénom_Nom.pdf ») | masqué avec le même coffre | le nom fuit souvent par là |
| Contact client dans une fiche de besoin | masqué | même règle pour tous les documents |

La table de correspondance (`pii_vault`) est stockée **à part**, jamais envoyée au LLM,
jamais incluse dans les exports dbt. Le dossier de compétences envoyé au client affiche par
défaut les **initiales** (`TF_ANONYMIZE_DOSSIERS=true`).

Détection : expressions régulières + heuristiques de mise en page (le nom est en tête de CV,
ou après « Nom : », « Contact : »…). Pour le texte libre, **Presidio** (NER spaCy) s'active
avec `TF_USE_PRESIDIO=true`.

**Contrôle automatique** : l'évaluation vérifie que chaque donnée personnelle connue du jeu
synthétique est absente de tout ce qui est stocké ou envoyé au LLM. La CI échoue en cas de fuite.

## 2. Choix du LLM et transferts hors UE

- Option **souveraine** : modèle local via llama.cpp (profil Docker `local-llm`) : aucune donnée
  ne quitte l'infrastructure.
- Option **cloud** : uniquement sur données pseudonymisées ; privilégier un fournisseur avec
  hébergement UE et engagement de non-réutilisation des données (Mistral, Azure OpenAI en région UE).
  Un contrat de sous-traitance (art. 28) reste nécessaire.

## 3. Durée de conservation et droit à l'effacement

- Chaque profil porte une date limite (`retention_until`, 24 mois par défaut, recommandation
  CNIL pour un vivier de candidats) ; Airflow purge chaque semaine les profils expirés.
- `DELETE /candidates/{id}` (ou l'écran « Journal & RGPD ») efface **tout** : profil, vecteur,
  propositions, coffre et document. L'action est tracée dans le journal d'audit.

## 4. Décision humaine et transparence (art. 22)

- Aucune proposition n'est envoyée sans **validation humaine** ; un rejet exige un motif.
- Chaque note est accompagnée d'une **justification** et des critères utilisés (compétences
  couvertes / manquantes, filtres appliqués) : la décision est explicable.
- Le prompt du juge interdit toute prise en compte d'éléments personnels.

## 5. Prospection B2B

Les emails sont envoyés à des adresses professionnelles, en lien avec la fonction du destinataire,
avec une mention de désinscription (art. L34-5 CPCE). Les prospects marqués `opt_out` sont exclus.
Les consultants y sont décrits de façon anonyme (métier, séniorité, compétences).

## 6. À valider avec le DPO avant la mise en production

- Base légale (intérêt légitime du vivier, information des candidats à la collecte).
- Inscription du traitement au registre (art. 30) et éventuelle AIPD (art. 35) : profilage.
- Chiffrement du coffre au repos (pgcrypto ou chiffrement applicatif), gestion des accès.
- Contrats avec les sous-traitants (fournisseur LLM, hébergeur, Boond).
