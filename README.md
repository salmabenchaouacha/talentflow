# TalentFlow — chaîne automatisée de staffing par IA

> Du besoin client reçu par email jusqu'au dossier de compétences validé, en quelques minutes,
> avec un humain qui garde la décision.

TalentFlow automatise le travail quotidien d'une ESN : lire les fiches de besoin, retrouver
les bons consultants dans le vivier, expliquer pourquoi, produire le dossier de compétences,
préparer la prospection et piloter le tout chaque semaine. Les données des candidats sont
pseudonymisées avant tout appel à un LLM.

```
Email « Nouveau besoin Data Engineer »
   │  n8n : pièce jointe → API
   ▼
Parsing (Docling / pypdf / docx) → Pseudonymisation RGPD → Extraction LLM (JSON validé Pydantic)
   ▼
Matching : PGVector top 30 → filtres métier (dispo, lieu, TJM, séniorité, langues)
           → pré-score explicable → LLM-as-a-judge (note /100 + justification)
   ▼
3 profils → email au commercial → validation dans l'interface (ou rejet motivé)
   ▼
Dossier Word généré → n8n l'envoie au client et crée le positionnement Boond
   ▼
PostgreSQL → dbt → tableau de bord hebdomadaire · bilan envoyé chaque lundi par n8n
```

## Démarrage en 1 minute (sans clé d'API, sans Docker)

```bash
pip install -e ".[all]"
make demo     # 60 CV et 15 besoins synthétiques → matching → validation simulée → 15 dossiers Word
make eval     # rapport d'évaluation : reports/evaluation.md
make test     # tests unitaires, intégration, garde-fous qualité
```

Pile complète (PostgreSQL + PGVector, API, interface, n8n, boîte mail de démo) :

```bash
make up && make n8n-import
# API      http://localhost:8000/docs     Interface  http://localhost:8501
# n8n      http://localhost:5678          Mailpit    http://localhost:8025
make up-batch   # + Airflow (http://localhost:8080) et Metabase (http://localhost:3000)
```

## Ce que fait chaque étape

| # | Étape | Comment | Technologies |
|---|---|---|---|
| 1 | **Collecte** | email (n8n, IMAP / Outlook), API, dossier de dépôt (Airflow), vivier Boond ; dédoublonnage par SHA-256 | n8n, FastAPI, Microsoft Graph, API Boond |
| 2 | **Parsing** | PDF, DOCX, TXT, MD, HTML ; Docling si installé (colonnes, tableaux, OCR) | Docling, pypdf, python-docx |
| 3 | **Pseudonymisation** | identifiants → jetons, données sensibles supprimées, coffre séparé, nom de fichier masqué | regex, Presidio (option) |
| 4 | **Extraction** | profil et besoin en JSON validé par Pydantic, boucle de correction si le LLM se trompe, repli sur des règles | LLM (OpenAI, Mistral, Groq, Gemini, llama.cpp, Ollama), Pydantic |
| 5 | **Matching** | entonnoir : similarité → filtres métier → pré-score → LLM-as-a-judge | PGVector (HNSW), scikit-learn, LLM |
| 6 | **Validation humaine** | interface commerciale : valider / rejeter avec motif ; machine à états + journal d'audit | Streamlit |
| 7 | **Dossier de compétences** | Word généré à partir du profil structuré, adapté au besoin, initiales par défaut | python-docx |
| 8 | **Veille & prospection** | demande du marché vs vivier (compétences à sourcer / à pousser), emails personnalisés en brouillon | LLM, Jinja2, n8n |
| 9 | **Pilotage** | KPI hebdomadaires (volumes, taux de validation, délais, motifs de rejet), bilan email du lundi | dbt, Metabase, Streamlit, n8n |
| 10 | **Évaluation** | jeu annoté, ablation, garde-fous en CI (précision, fuites de données) | Pytest, GitHub Actions |

Détails : [architecture](docs/architecture.md) · [RGPD](docs/rgpd.md) · [workflows n8n](docs/n8n.md) ·
[scénario de démo](docs/demo.md)

## Correspondance avec le besoin d'une ESN

| Besoin | Dans TalentFlow |
|---|---|
| Automatiser le sourcing et le tri des CV avec l'IA | étapes 1 à 5 |
| Suivre les besoins du marché et les rapprocher du vivier | matching + veille marché (`/market`) |
| Générer automatiquement les dossiers de compétences | étape 7 |
| Préparer des campagnes de prospection personnalisées | étape 8, workflow n8n 03 |
| Connecter CRM, Boond, Microsoft 365, assistants IA | connecteurs Boond et Graph, API REST, 4 workflows n8n |
| Piloter avec un tableau de bord hebdomadaire | dbt + Metabase / Streamlit, workflow n8n 04 |
| Sensibilité RGPD | pseudonymisation, coffre séparé, rétention, effacement, audit, LLM local possible |
| Une chaîne mesurable, pas un POC | évaluation automatique en CI, KPI, journal d'audit, Docker |

## Évaluation (données synthétiques)

`make eval` ingère le jeu synthétique, puis mesure la pseudonymisation, l'extraction et le
matching selon trois configurations (étude d'ablation). Résultats en mode hors-ligne
(extracteur à règles, juge heuristique, embeddings par hachage) :

| Configuration | Precision@3 | nDCG@5 | Précision des 3 propositions |
|---|---|---|---|
| Similarité vectorielle seule | 0,58 | 0,76 | 0,58 |
| + filtres métier et pré-score | 0,84 | 1,00 | 0,84 |
| + juge (score combiné) | 0,84 | 1,00 | 0,91 |

Pseudonymisation : 0 fuite sur 278 valeurs personnelles vérifiées.

**À lire avec prudence** : les CV synthétiques suivent un format connu de l'extracteur à règles
et les jugements de pertinence sont dérivés des attributs de génération. Ces chiffres servent à
détecter les régressions et à comparer des variantes, pas à annoncer une performance sur de
vrais CV. Prochaine étape : annoter à la main `data/eval/annotations_a_relire.csv` et relancer
l'évaluation avec un vrai LLM (`TF_LLM_PROVIDER=…`) ; le rapport inclut alors le coût et la
latence. Rapport complet : [docs/evaluation_synthetique.md](docs/evaluation_synthetique.md).

## Configuration

Tout se règle dans `.env` (modèle : [.env.example](.env.example)).

| Variable | Valeurs | Défaut |
|---|---|---|
| `TF_LLM_PROVIDER` | `offline`, `openai`, `mistral`, `groq`, `gemini`, `ollama`, `llamacpp` | `offline` |
| `TF_EMBEDDING_PROVIDER` | `hashing`, `openai`, `mistral`, `ollama`, `sentence-transformers` | `hashing` |
| `TF_DATABASE_URL` | `sqlite:///…` ou `postgresql://…` | SQLite |
| `TF_JUDGE_WEIGHT` / `TF_JUDGE_TOP_N` | poids du juge, nombre de profils jugés | 0,4 / 8 |
| `TF_RETENTION_DAYS` | durée de conservation des profils | 730 |
| `TF_ANONYMIZE_DOSSIERS` | initiales dans le dossier client | `true` |

**LLM local** : déposer un modèle GGUF dans `models/model.gguf`, puis
`docker compose --profile local-llm up` et `TF_LLM_PROVIDER=llamacpp`. Aucun CV ne quitte la machine.

## API

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/documents`, `/cvs`, `/needs` | ingérer un fichier (type deviné si absent) |
| `POST` | `/needs/{id}/match?mode=full` | lancer le matching |
| `GET` | `/matches?need_id=&status=` | propositions et justifications |
| `POST` | `/matches/{id}/validate` · `/reject` · `/sent` | décision humaine, envoi |
| `GET` | `/matches/{id}/dossier` | dossier Word |
| `GET` | `/metrics/weekly` · `/market` | pilotage, veille |
| `POST` | `/prospection/generate` · `/prospection/{id}/approve` | prospection |
| `DELETE` | `/candidates/{id}` · `POST /gdpr/purge` | droit à l'effacement, rétention |
| `GET` | `/audit` | journal d'audit |

Authentification par en-tête `X-API-Key`. Documentation interactive : `/docs`.

## Structure

```
src/talentflow/
  ingestion/     parsing des documents, sources (dossier, IMAP)
  privacy/       pseudonymisation, coffre, initiales
  llm/           clients LLM (sortie JSON validée, retries), prompts versionnés
  extraction/    référentiel de compétences, extracteurs LLM / règles
  embeddings/    hashing, OpenAI-compatible, sentence-transformers
  matching/      filtres métier, scores, juge LLM, moteur en entonnoir
  storage/       dépôt SQLite / PostgreSQL + PGVector, indicateurs
  generation/    dossier Word, veille marché, prospection
  connectors/    BoondManager, Microsoft Graph
  workflow/      validation humaine (machine à états), webhook n8n
  evaluation/    données synthétiques, métriques, rapport
  api/           FastAPI (+ faux serveur Boond pour la démo)
dashboard/       interface Streamlit
n8n/workflows/   4 workflows importables
airflow/dags/    rattrapage quotidien, maintenance hebdomadaire
dbt/             modèles staging / marts + tests
tests/           tests unitaires, d'intégration (API, pipeline, n8n) et garde-fous qualité
docs/exemples/   exemple de dossier de compétences généré (DOCX + PDF)
```

## Limites connues et suite

- Les chemins de l'API Boond suivent la documentation publique et sont à vérifier sur
  l'instance cible ; le faux serveur sert la démo.
- L'extracteur à règles est une base hors-ligne ; en production, l'extraction passe par le LLM
  (avec repli automatique sur les règles en cas d'erreur).
- Boucle d'amélioration à brancher : réutiliser les motifs de rejet pour ajuster les poids du
  pré-score et enrichir le prompt du juge.
- Chiffrement du coffre au repos et revue DPO avant mise en production (voir [RGPD](docs/rgpd.md)).

## Licence

MIT — Salma Ben Chaouacha
