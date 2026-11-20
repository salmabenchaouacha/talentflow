"""Référentiel de compétences : normalisation des synonymes et familles techniques.

Sert à trois choses :
1. normaliser ce que renvoie le LLM ("ReactJS", "react.js" → "React") ;
2. détecter les compétences dans un texte (extracteur hors-ligne) ;
3. donner un crédit partiel à une compétence proche (PostgreSQL ≈ MySQL).
"""

from __future__ import annotations

import re
from functools import lru_cache

# canonique -> (famille, [alias])
TAXONOMY: dict[str, tuple[str, list[str]]] = {
    # Langages
    "Python": ("lang", ["python3", "py"]),
    "Java": ("lang-jvm", ["java 17", "java 11", "j2ee", "jee", "java ee"]),
    "Kotlin": ("lang-jvm", []),
    "Scala": ("lang-jvm", []),
    "C#": ("lang-dotnet", ["csharp", "c sharp"]),
    "JavaScript": ("lang-js", ["js", "javascript es6", "es6"]),
    "TypeScript": ("lang-js", ["ts"]),
    "Go": ("lang", ["golang"]),
    "C++": ("lang-c", ["cpp"]),
    "C": ("lang-c", []),
    "PHP": ("lang-php", []),
    "R": ("lang-data", []),
    "SQL": ("sql", ["t-sql", "pl/sql", "plsql", "tsql"]),
    "Bash": ("ops-script", ["shell", "shell scripting"]),
    # Back-end
    "Spring Boot": ("fw-jvm", ["spring", "springboot", "spring framework"]),
    "Hibernate": ("fw-jvm", ["jpa"]),
    ".NET": ("fw-dotnet", [".net core", "dotnet", "asp.net", "asp.net core", ".net 8"]),
    "Django": ("fw-py", []),
    "FastAPI": ("fw-py", ["fast api"]),
    "Flask": ("fw-py", []),
    "Node.js": ("fw-js-back", ["nodejs", "node", "express", "express.js", "nestjs"]),
    "Symfony": ("fw-php", ["laravel"]),
    "REST API": ("api", ["rest", "api rest", "restful", "openapi", "swagger"]),
    "GraphQL": ("api", []),
    "Microservices": ("archi", ["micro-services", "microservice"]),
    "Kafka": ("messaging", ["apache kafka"]),
    "RabbitMQ": ("messaging", []),
    # Front-end
    "React": ("fw-front", ["reactjs", "react.js", "next.js", "nextjs"]),
    "Angular": ("fw-front", ["angularjs"]),
    "Vue.js": ("fw-front", ["vue", "vuejs", "nuxt"]),
    "HTML/CSS": ("front-base", ["html", "css", "html5", "css3", "tailwind", "sass"]),
    "React Native": ("mobile", []),
    "Flutter": ("mobile", ["dart"]),
    # Bases de données
    "PostgreSQL": ("db-sql", ["postgres", "postgresql 15"]),
    "MySQL": ("db-sql", ["mariadb"]),
    "Oracle": ("db-sql", ["oracle db"]),
    "SQL Server": ("db-sql", ["mssql", "microsoft sql server"]),
    "MongoDB": ("db-nosql", ["mongo"]),
    "Redis": ("db-nosql", []),
    "Elasticsearch": ("search", ["elastic", "elk", "opensearch"]),
    # Data engineering
    "Airflow": ("orchestration", ["apache airflow"]),
    "dbt": ("data-transform", ["data build tool"]),
    "Spark": ("bigdata", ["pyspark", "apache spark"]),
    "Databricks": ("bigdata", []),
    "Snowflake": ("dwh", []),
    "BigQuery": ("dwh", ["google bigquery"]),
    "ETL": ("data-transform", ["elt", "talend", "informatica", "ssis"]),
    "Pandas": ("data-py", []),
    "Power BI": ("bi", ["powerbi", "dax"]),
    "Tableau": ("bi", []),
    "Metabase": ("bi", []),
    # IA / ML
    "Machine Learning": ("ml", ["ml", "apprentissage automatique"]),
    "Deep Learning": ("ml-dl", ["dl", "réseaux de neurones"]),
    "Scikit-learn": ("ml", ["sklearn", "scikit learn"]),
    "PyTorch": ("ml-dl", ["torch"]),
    "TensorFlow": ("ml-dl", ["keras"]),
    "NLP": ("ml-nlp", ["traitement du langage naturel", "tal"]),
    "Computer Vision": ("ml-cv", ["vision par ordinateur", "opencv"]),
    "LLM": ("genai", ["large language models", "gpt", "ia générative", "genai", "generative ai"]),
    "RAG": ("genai", ["retrieval augmented generation"]),
    "LangChain": ("genai-fw", []),
    "LangGraph": ("genai-fw", []),
    "Hugging Face": ("genai-fw", ["huggingface", "transformers"]),
    "Prompt Engineering": ("genai", []),
    "MLOps": ("mlops", ["mlflow", "kubeflow"]),
    "Vector Databases": ("genai-db", ["qdrant", "pgvector", "chromadb", "pinecone", "weaviate"]),
    # Cloud / DevOps
    "AWS": ("cloud", ["amazon web services", "ec2", "s3", "lambda"]),
    "Azure": ("cloud", ["microsoft azure", "azure devops"]),
    "GCP": ("cloud", ["google cloud", "google cloud platform"]),
    "Docker": ("container", ["conteneurisation"]),
    "Kubernetes": ("container-orch", ["k8s", "openshift", "aks", "eks", "gke"]),
    "Terraform": ("iac", ["terragrunt"]),
    "Ansible": ("iac", []),
    "CI/CD": ("cicd", ["gitlab ci", "github actions", "jenkins", "intégration continue"]),
    "Git": ("vcs", ["github", "gitlab"]),
    "Linux": ("os", ["unix", "debian", "ubuntu", "red hat"]),
    "Prometheus": ("observability", ["grafana"]),
    # Automatisation / outils métier
    "n8n": ("automation", []),
    "Make": ("automation", ["integromat"]),
    "Power Automate": ("automation", ["microsoft power automate"]),
    "Zapier": ("automation", []),
    "Microsoft 365": ("office", ["office 365", "m365", "sharepoint", "microsoft graph"]),
    "Salesforce": ("crm", []),
    "SAP": ("erp", ["sap s/4hana", "sap fico", "sap mm"]),
    # Qualité / méthodes
    "Selenium": ("qa", ["cypress", "playwright"]),
    "Tests automatisés": ("qa", ["pytest", "junit", "tests unitaires", "tdd"]),
    "Agile Scrum": ("method", ["scrum", "agile", "safe", "kanban"]),
    "Gestion de projet": ("pm", ["pmo", "chef de projet", "prince2", "pmp"]),
    "Cybersécurité": ("security", ["sécurité", "iso 27001", "pentest", "soc"]),
    "RGPD": ("compliance", ["gdpr"]),
}

FAMILY_RELATIONS: dict[str, set[str]] = {
    "lang-jvm": {"fw-jvm"},
    "fw-jvm": {"lang-jvm"},
    "lang-dotnet": {"fw-dotnet"},
    "fw-dotnet": {"lang-dotnet"},
    "fw-py": {"lang"},
    "lang-js": {"fw-front", "fw-js-back"},
    "fw-front": {"lang-js"},
    "dwh": {"bigdata"},
    "bigdata": {"dwh"},
    "ml": {"ml-dl", "ml-nlp"},
    "ml-dl": {"ml", "ml-nlp", "ml-cv"},
    "ml-nlp": {"genai", "ml-dl"},
    "genai": {"genai-fw", "ml-nlp"},
    "genai-fw": {"genai"},
    "container": {"container-orch"},
    "container-orch": {"container"},
}


@lru_cache(maxsize=1)
def _alias_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for canon, (_family, aliases) in TAXONOMY.items():
        index[canon.lower()] = canon
        for alias in aliases:
            index[alias.lower()] = canon
    return index


def normalize_skill(raw: str) -> str:
    """Renvoie le nom canonique ; garde le libellé d'origine (nettoyé) si inconnu."""
    if not raw:
        return ""
    cleaned = re.sub(r"\s+", " ", raw.strip().strip(".,;:")).strip()
    return _alias_index().get(cleaned.lower(), cleaned)


def family(skill: str) -> str | None:
    entry = TAXONOMY.get(normalize_skill(skill))
    return entry[0] if entry else None


def relatedness(a: str, b: str) -> float:
    """1.0 si identiques, 0.5 si même famille, 0.25 si familles voisines, sinon 0."""
    a, b = normalize_skill(a), normalize_skill(b)
    if a == b:
        return 1.0
    fa, fb = family(a), family(b)
    if not fa or not fb:
        return 0.0
    if fa == fb:
        return 0.5
    if fb in FAMILY_RELATIONS.get(fa, set()):
        return 0.25
    return 0.0


@lru_cache(maxsize=1)
def _detection_patterns() -> list[tuple[re.Pattern[str], str]]:
    patterns = []
    # Les alias les plus longs d'abord pour éviter "Java" dans "JavaScript".
    for alias, canon in sorted(_alias_index().items(), key=lambda kv: -len(kv[0])):
        if len(alias) <= 2 and alias not in {"r", "c", "go", "js", "ts", "ml", "dl"}:
            continue
        if alias in {"r", "c", "go", "make", "tal", "py", "node", "spring", "rest", "safe",
                     "agile", "elastic", "vue", "soc", "sécurité", "html", "css", "dart"}:
            # alias ambigus : uniquement en contexte de liste (séparateurs explicites)
            pat = re.compile(
                rf"(?:(?<=[,;:/(•\-])|(?<=^))\s*{re.escape(alias)}\s*(?=[,;/)•\n]|$)",
                re.IGNORECASE | re.MULTILINE,
            )
        else:
            pat = re.compile(
                rf"(?<![\w+#.]){re.escape(alias)}(?![\w+#])", re.IGNORECASE
            )
        patterns.append((pat, canon))
    return patterns


def detect_skills(text: str) -> list[str]:
    """Détecte les compétences connues dans un texte libre (ordre d'apparition)."""
    found: dict[str, int] = {}
    for pat, canon in _detection_patterns():
        m = pat.search(text)
        if m and (canon not in found or m.start() < found[canon]):
            found[canon] = m.start()
    return [c for c, _ in sorted(found.items(), key=lambda kv: kv[1])]
