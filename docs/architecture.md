# Architecture

## Vue d'ensemble

```mermaid
flowchart LR
    subgraph Entrées
        M[Boîte mail<br/>Outlook / IMAP]
        B[BoondManager<br/>vivier]
        D[Dossier de dépôt]
        J[Offres d'emploi<br/>publiques]
    end

    subgraph n8n["n8n — temps réel"]
        W1[01 Réception email]
        W2[02 Envoi après validation]
        W3[03 Prospection]
        W4[04 Bilan hebdo]
    end

    subgraph API["API TalentFlow (FastAPI)"]
        P[Parsing<br/>Docling / pypdf / docx]
        R[Pseudonymisation<br/>regex + Presidio]
        E[Extraction LLM<br/>schéma Pydantic]
        V[Embeddings]
        MT[Matching<br/>filtres → pré-score → juge LLM]
        G[Dossier Word]
        PR[Veille & prospection]
    end

    subgraph Stockage
        PG[(PostgreSQL<br/>+ PGVector)]
        VA[(Coffre PII<br/>table séparée)]
    end

    subgraph Pilotage
        UI[Streamlit<br/>validation humaine]
        DBT[dbt]
        MB[Metabase / Power BI]
    end

    AF[Airflow — batch hebdo<br/>sync Boond, embeddings,<br/>purge RGPD, éval, dbt]

    M --> W1 --> P
    D --> AF
    B --> AF --> P
    P --> R --> E --> V --> PG
    R --> VA
    E --> MT
    PG --> MT --> UI
    UI -- valider --> G --> W2
    W2 -- email client --> M
    W2 -- positionnement --> B
    J --> PR --> W3
    PG --> DBT --> MB
    PG --> W4
```

## Pourquoi ce découpage n8n / Python / Airflow

| Besoin | Outil | Raison |
|---|---|---|
| Réagir à un email en quelques minutes, brancher Outlook, Boond, le CRM | **n8n** | Connecteurs prêts à l'emploi, modifiable par l'équipe sans redéploiement |
| Extraction, pseudonymisation, matching, génération | **Python (API)** | Logique testable (Pytest), versionnée, évaluée à chaque modification |
| Traitements lourds et planifiés, avec reprise et historique | **Airflow** | Ré-indexation, purge RGPD, évaluation de non-régression, dbt |
| Indicateurs du tableau de bord | **dbt + Metabase** et Streamlit | Modèles SQL testés ; Streamlit pour le quotidien des commerciaux |

## Le matching en entonnoir

```mermaid
flowchart TD
    A[Vivier complet] -->|similarité cosinus PGVector, top 30| B[30 profils]
    B -->|filtres métier bloquants : dispo, lieu, TJM, séniorité, langues| C[profils éligibles]
    C -->|pré-score explicable : couverture 45 %, similarité 25 %,<br/>séniorité 15 %, profondeur 15 %| D[classement]
    D -->|LLM-as-a-judge sur les 8 premiers : note /100 + justification| E[score final = 0,6 pré-score + 0,4 juge]
    E --> F[3 propositions → validation humaine]
```

- **Pourquoi des filtres avant le LLM** : un profil indisponible ou hors budget ne doit pas être
  proposé, quelle que soit sa note. Les filtres sont déterministes, explicables et gratuits.
- **Pourquoi un juge LLM** : il lit les expériences comme un commercial (contexte, récence),
  ce qu'une simple similarité ne fait pas, et produit une justification lisible.
- **Pourquoi le juge ne décide pas seul** : son score est combiné au pré-score, et un humain valide.

## Modèle de données

| Table | Contenu | Données personnelles |
|---|---|---|
| `documents` | texte **pseudonymisé**, nom de fichier masqué, empreinte SHA-256 (dédoublonnage) | non |
| `pii_vault` | correspondance jeton → valeur réelle | **oui**, isolée, effacée à la demande |
| `candidates` | profil structuré (JSON validé), date limite de conservation | non |
| `candidate_embeddings` | vecteur `vector(n)` + index HNSW | non |
| `needs` | fiche de besoin structurée | non (contact client masqué) |
| `matches` | scores, justification, statut, relecteur, motif de rejet | non |
| `prospection_emails` | brouillons, statut (draft → approved → sent) | non |
| `audit_log` | qui a fait quoi, quand | identifiant du relecteur |

## Fournisseurs interchangeables

Tout passe par des interfaces ; on change de fournisseur par variable d'environnement.

| Composant | Hors-ligne (démo, CI) | Cloud | Local (souverain) |
|---|---|---|---|
| LLM | règles + juge heuristique | OpenAI, Mistral, Groq, Gemini | llama.cpp, Ollama |
| Embeddings | hashing (scikit-learn) | OpenAI, Mistral | sentence-transformers, Ollama |
| Base | SQLite | PostgreSQL managé | PostgreSQL + PGVector |
