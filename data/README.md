# Données

Toutes les données de ce dossier sont **synthétiques** (générées par
`talentflow generate-data`, graine 42) : les noms, emails, téléphones et adresses sont
inventés et servent à tester la pseudonymisation. Aucune donnée réelle de candidat.

| Dossier | Contenu |
|---|---|
| `synthetic/cvs/` | CV de consultants fictifs (DOCX, TXT, MD) |
| `synthetic/needs/` | fiches de besoin de clients fictifs (TXT, email MD, DOCX) |
| `synthetic/ground_truth.json` | attributs réels de chaque profil et pertinence besoin × consultant |
| `synthetic/boond_*.json` | export au format BoondManager pour le faux serveur Boond |
| `market/job_posts.csv` | offres d'emploi publiques fictives (veille marché) |
| `market/prospects.csv` | prospects fictifs (un en opposition, exclu de la prospection) |
| `eval/annotations_a_relire.csv` | paires à annoter à la main avant de citer un résultat |
| `inbox/` | dossier de dépôt surveillé par Airflow |
