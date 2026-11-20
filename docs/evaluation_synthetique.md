# Rapport d'évaluation TalentFlow

Jeu synthétique : 60 consultants, 15 besoins (graine 42, date de référence 2026-11-20).
Configuration : LLM `offline`, embeddings `hashing`.

> Résultats sur données synthétiques, à usage de non-régression et de comparaison de variantes. Les jugements de pertinence sont à relire à la main (`data/eval/annotations_a_relire.csv`).

## 1. Pseudonymisation (RGPD)
- Valeurs personnelles vérifiées : 278
- Fuites dans les données stockées / envoyées au LLM : **0** (taux 0.00%)

## 2. Extraction
| Mesure | Valeur |
|---|---|
| extractor | rules |
| cv_skills_f1 | 0.973 |
| cv_years_mae | 0.373 |
| cv_rate_accuracy | 1.000 |
| cv_availability_accuracy | 1.000 |
| cv_city_accuracy | 1.000 |
| need_required_skills_f1 | 0.971 |
| need_min_years_accuracy | 1.000 |
| need_max_rate_accuracy | 1.000 |

## 3. Matching — étude d'ablation
| Mode | Precision@3 | nDCG@5 | MRR | Précision des propositions | Rappel récupération | Exclus à tort |
|---|---|---|---|---|---|---|
| Similarité seule | 0.578 | 0.756 | 0.789 | 0.578 | 1.000 | — |
| + filtres métier & pré-score | 0.844 | 0.996 | 1.000 | 0.844 | 1.000 | 0.000 |
| + LLM-as-a-judge | 0.844 | 0.998 | 1.000 | 0.911 | 1.000 | 0.000 |

## 4. Détail par besoin (mode complet)

| Besoin | Profils pertinents | Proposés (pertinence 0/1/2) |
|---|---|---|
| GT-N01 · Ingénieur Data confirmé | 3 | GT-C020 (2), GT-C044 (2), GT-C027 (1) |
| GT-N02 · AI Engineer confirmé | 3 | GT-C035 (2), GT-C019 (2), GT-C026 (2) |
| GT-N03 · Ingénieur Java Spring junior | 4 | GT-C047 (2), GT-C015 (2), GT-C024 (1) |
| GT-N04 · Data Scientist NLP junior | 2 | GT-C060 (2), GT-C008 (2), GT-C036 (0) |
| GT-N05 · Ingénieur SRE senior | 5 | GT-C007 (2), GT-C037 (2), GT-C038 (1) |
| GT-N06 · Développeur C# .NET senior | 2 | GT-C004 (2), GT-C054 (2), GT-C016 (0) |
| GT-N07 · Développeur Front-end senior | 2 | GT-C025 (2), GT-C034 (2), GT-C028 (0) |
| GT-N08 · Consultant Automatisation confirmé | 2 | GT-C059 (2), GT-C056 (2) |
| GT-N09 · Ingénieur Data senior | 4 | GT-C018 (2), GT-C027 (2), GT-C022 (2) |
| GT-N10 · Développeur Back-end Java senior | 2 | GT-C013 (2), GT-C032 (2), GT-C004 (0) |
| GT-N11 · Ingénieur IA senior | 2 | GT-C014 (2), GT-C011 (2) |
| GT-N12 · Ingénieur QA confirmé | 3 | GT-C028 (2), GT-C042 (2), GT-C006 (2) |
| GT-N13 · DevOps Cloud senior | 6 | GT-C038 (2), GT-C055 (2), GT-C007 (1) |
| GT-N14 · Data Scientist NLP confirmé | 3 | GT-C001 (2), GT-C036 (2), GT-C027 (1) |
| GT-N15 · Product Owner expert | 2 | GT-C033 (2), GT-C021 (2) |
