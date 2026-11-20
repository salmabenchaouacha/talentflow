# Workflows n8n

Les 4 workflows sont dans `n8n/workflows/`. Ils appellent l'API TalentFlow avec l'en-tête
`X-API-Key` ; l'URL et la clé viennent des variables d'environnement (`$env.TF_API_BASE_URL`,
`$env.TF_API_KEY`), d'où `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` dans `docker-compose.yml`.

| Workflow | Déclencheur | Ce qu'il fait |
|---|---|---|
| **01 Réception email** | nouvel email non lu (IMAP) | une pièce jointe = un document → `POST /documents` → si c'est un besoin : `POST /needs/{id}/match` → email au commercial avec les 3 profils, leur justification et le lien de validation |
| **02 Envoi après validation** | webhook appelé par l'API quand un commercial valide | télécharge le dossier Word → l'envoie au client → crée le positionnement dans Boond → `POST /matches/{id}/sent` |
| **03 Prospection** | mardi 9h + toutes les heures | génère les brouillons et demande une relecture ; envoie les emails approuvés puis les marque envoyés |
| **04 Bilan hebdomadaire** | lundi 8h | `GET /metrics/weekly` → tableau HTML → email à la direction |

## Mise en place (démo locale)

```bash
make up            # API, interface, n8n, Mailpit, Postgres
make n8n-import    # importe les 4 workflows
```

1. Ouvrir n8n : http://localhost:5678 et créer le compte propriétaire.
2. Créer les identifiants :
   - **SMTP** → hôte `mailpit`, port `1025`, sans authentification ni SSL (démo) ;
   - **IMAP** → la boîte qui reçoit les besoins (en démo : un compte Gmail/Outlook de test).
3. Ouvrir chaque workflow, sélectionner ces identifiants dans les nœuds email, puis l'activer.
4. Tous les emails envoyés arrivent dans Mailpit : http://localhost:8025

## En production chez une ESN sous Microsoft 365

- Remplacer le nœud IMAP par **Microsoft Outlook Trigger** (boîte partagée `besoins@…`).
- Remplacer l'envoi SMTP par **Microsoft Outlook → Send** ou Teams pour l'alerte commerciale.
- Remplacer `/mock/boond/positionings` par l'URL de l'API Boond de l'instance (le connecteur
  Python `talentflow.connectors.boond` gère l'authentification JWT si l'on préfère passer par l'API).
- Le même enchaînement se transpose à **Power Automate** ou **Make** : chaque étape n'est qu'un
  appel HTTP à l'API TalentFlow.
