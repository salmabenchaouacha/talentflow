# Scénario de démo (vidéo de 2 minutes)

Préparation : `make up && make n8n-import`, workflows activés, `make demo` lancé une fois
pour remplir le vivier (ou `talentflow ingest data/synthetic/cvs --kind cv`).

| Temps | À l'écran | À dire |
|---|---|---|
| 0:00 | Schéma d'architecture (README) | « Un besoin client arrive par email ; voici ce qui se passe sans intervention. » |
| 0:15 | Envoi d'un email avec `Besoin_GT-N01_….md` en pièce jointe | « L'objet contient "besoin" : n8n le reconnaît. » |
| 0:25 | Exécution du workflow 01 dans n8n | « Pièce jointe extraite, envoyée à l'API, pseudonymisée, structurée par le LLM, puis matching. » |
| 0:45 | Email reçu dans Mailpit : 3 profils + justification | « Le commercial reçoit trois profils, chacun avec sa note et la raison. » |
| 1:00 | Interface Streamlit : fiche de besoin structurée, propositions | « Compétences couvertes, manquantes, points d'attention (TJM, disponibilité). » |
| 1:15 | Clic « Valider » sur le premier, rejet motivé du troisième | « Rien ne part sans validation ; le motif de rejet sert à améliorer le matching. » |
| 1:25 | Workflow 02 : email au client avec le dossier Word ; positionnement Boond | « Le dossier de compétences est généré au format de l'entreprise, avec les initiales. » |
| 1:40 | Ouverture du dossier Word | « Compétences demandées en premier, avis d'adéquation joint. » |
| 1:50 | Tableau de bord hebdo + écran RGPD (journal, effacement) | « Chaque semaine : volumes, taux de validation, délais. Et chaque action est tracée. » |
