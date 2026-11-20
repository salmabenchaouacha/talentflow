"""Configuration centralisée, lue depuis les variables d'environnement (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Charge un fichier .env minimal (KEY=VALUE) sans dépendance externe."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # --- Général -----------------------------------------------------------
    env: str = "dev"
    brand_name: str = "TalentFlow"
    data_dir: Path = Path("data")
    output_dir: Path = Path("outputs")

    # --- Base de données ----------------------------------------------------
    # sqlite:///data/talentflow.db  ou  postgresql://user:pwd@host:5432/db
    database_url: str = "sqlite:///data/talentflow.db"

    # --- LLM ----------------------------------------------------------------
    # offline | openai | groq | mistral | ollama | llamacpp | gemini
    llm_provider: str = "offline"
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_temperature: float = 0.0
    llm_timeout_s: int = 60
    llm_max_retries: int = 2

    # --- Embeddings ---------------------------------------------------------
    # hashing (hors-ligne) | openai | ollama | sentence-transformers
    embedding_provider: str = "hashing"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 512
    embedding_base_url: str = ""
    embedding_api_key: str = ""

    # --- Matching -----------------------------------------------------------
    retrieval_k: int = 30            # candidats récupérés par similarité vectorielle
    judge_top_n: int = 8             # candidats notés par le LLM-as-a-judge
    proposal_top_k: int = 3          # profils proposés au commercial
    judge_weight: float = 0.4        # poids du juge dans le score final
    rate_tolerance: float = 0.10     # dépassement de TJM toléré (10 %)
    availability_tolerance_days: int = 30

    # --- RGPD ---------------------------------------------------------------
    use_presidio: bool = False
    retention_days: int = 730        # 24 mois, recommandation CNIL pour un vivier
    anonymize_dossiers: bool = True  # initiales au lieu du nom dans le dossier client

    # --- Sécurité / intégrations -------------------------------------------
    api_key: str = "change-me"
    api_base_url: str = "http://localhost:8000"
    review_ui_url: str = "http://localhost:8501"
    n8n_validation_webhook_url: str = ""
    boond_base_url: str = ""
    boond_client_token: str = ""
    boond_client_key: str = ""
    boond_user_token: str = ""
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""
    graph_mailbox: str = ""

    extra: dict = field(default_factory=dict)

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith(("postgres://", "postgresql://"))

    @property
    def sqlite_path(self) -> Path:
        return Path(self.database_url.replace("sqlite:///", "", 1))


def get_settings(env_file: str | Path = ".env") -> Settings:
    _load_dotenv(Path(env_file))
    return Settings(
        env=_env("TF_ENV", "dev"),
        brand_name=_env("TF_BRAND_NAME", "TalentFlow"),
        data_dir=Path(_env("TF_DATA_DIR", "data")),
        output_dir=Path(_env("TF_OUTPUT_DIR", "outputs")),
        database_url=_env("TF_DATABASE_URL", "sqlite:///data/talentflow.db"),
        llm_provider=_env("TF_LLM_PROVIDER", "offline").lower(),
        llm_model=_env("TF_LLM_MODEL", "gpt-4o-mini"),
        llm_base_url=_env("TF_LLM_BASE_URL", ""),
        llm_api_key=_env("TF_LLM_API_KEY", ""),
        llm_temperature=_env_float("TF_LLM_TEMPERATURE", 0.0),
        llm_timeout_s=_env_int("TF_LLM_TIMEOUT_S", 60),
        llm_max_retries=_env_int("TF_LLM_MAX_RETRIES", 2),
        embedding_provider=_env("TF_EMBEDDING_PROVIDER", "hashing").lower(),
        embedding_model=_env("TF_EMBEDDING_MODEL", "text-embedding-3-small"),
        embedding_dim=_env_int("TF_EMBEDDING_DIM", 512),
        embedding_base_url=_env("TF_EMBEDDING_BASE_URL", ""),
        embedding_api_key=_env("TF_EMBEDDING_API_KEY", ""),
        retrieval_k=_env_int("TF_RETRIEVAL_K", 30),
        judge_top_n=_env_int("TF_JUDGE_TOP_N", 8),
        proposal_top_k=_env_int("TF_PROPOSAL_TOP_K", 3),
        judge_weight=_env_float("TF_JUDGE_WEIGHT", 0.4),
        rate_tolerance=_env_float("TF_RATE_TOLERANCE", 0.10),
        availability_tolerance_days=_env_int("TF_AVAILABILITY_TOLERANCE_DAYS", 30),
        use_presidio=_env_bool("TF_USE_PRESIDIO", False),
        retention_days=_env_int("TF_RETENTION_DAYS", 730),
        anonymize_dossiers=_env_bool("TF_ANONYMIZE_DOSSIERS", True),
        api_key=_env("TF_API_KEY", "change-me"),
        api_base_url=_env("TF_API_BASE_URL", "http://localhost:8000"),
        review_ui_url=_env("TF_REVIEW_UI_URL", "http://localhost:8501"),
        n8n_validation_webhook_url=_env("TF_N8N_VALIDATION_WEBHOOK_URL", ""),
        boond_base_url=_env("TF_BOOND_BASE_URL", ""),
        boond_client_token=_env("TF_BOOND_CLIENT_TOKEN", ""),
        boond_client_key=_env("TF_BOOND_CLIENT_KEY", ""),
        boond_user_token=_env("TF_BOOND_USER_TOKEN", ""),
        graph_tenant_id=_env("TF_GRAPH_TENANT_ID", ""),
        graph_client_id=_env("TF_GRAPH_CLIENT_ID", ""),
        graph_client_secret=_env("TF_GRAPH_CLIENT_SECRET", ""),
        graph_mailbox=_env("TF_GRAPH_MAILBOX", ""),
    )
