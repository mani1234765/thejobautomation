"""Curated skill taxonomy: canonical names, aliases, categories and related-skill edges.

This is what makes matching *semantic* rather than literal: "k8s", "Kubernetes" and "EKS" all
normalise to Kubernetes, and a job asking for "PostgreSQL" gives partial credit for "MySQL"
through a `related` edge. Ambiguous short tokens (Go, R, C) are matched case-sensitively and
only in unambiguous forms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class SkillDef:
    name: str
    category: str
    aliases: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    case_sensitive_aliases: tuple[str, ...] = field(default=())


def _s(name: str, category: str, aliases: str = "", related: str = "", cs: str = "") -> SkillDef:
    split = lambda v: tuple(x.strip() for x in v.split("|") if x.strip())  # noqa: E731
    return SkillDef(name, category, split(aliases), split(related), split(cs))


SKILLS: tuple[SkillDef, ...] = (
    # Languages
    _s("Python", "language", "python3", "Django|FastAPI|Flask|Pandas"),
    _s("JavaScript", "language", "javascript|ecmascript|es6|js", "TypeScript|Node.js"),
    _s("TypeScript", "language", "typescript|ts", "JavaScript"),
    _s("Java", "language", "java", "Kotlin|Spring", cs="Java"),
    _s("Kotlin", "language", "kotlin", "Java|Android"),
    _s("Go", "language", "golang", "", cs="Go"),
    _s("Rust", "language", "rust", "C++"),
    _s("C++", "language", "c++|cpp", "C|Rust"),
    _s("C", "language", "", "C++", cs="C"),
    _s("C#", "language", "c#|csharp|c sharp", ".NET"),
    _s("Ruby", "language", "ruby", "Ruby on Rails"),
    _s("PHP", "language", "php", "Laravel"),
    _s("Swift", "language", "swift", "iOS"),
    _s("Scala", "language", "scala", "Spark|Java"),
    _s("R", "language", "r programming|rstudio", "Statistics", cs="R"),
    _s("SQL", "language", "sql|t-sql|pl/sql|plsql", "PostgreSQL|MySQL"),
    _s("Bash", "language", "bash|shell scripting|shell script|zsh", "Linux"),
    _s("Dart", "language", "dart", "Flutter"),
    _s("MATLAB", "language", "matlab", ""),
    # Frontend
    _s("React", "frontend", "react|react.js|reactjs", "Next.js|Redux|Vue.js"),
    _s("Next.js", "frontend", "next.js|nextjs", "React"),
    _s("Vue.js", "frontend", "vue|vue.js|vuejs|nuxt", "React"),
    _s("Angular", "frontend", "angular|angularjs", "React|TypeScript"),
    _s("Svelte", "frontend", "svelte|sveltekit", "React"),
    _s("Redux", "frontend", "redux|redux toolkit", "React"),
    _s("HTML", "frontend", "html|html5", "CSS"),
    _s("CSS", "frontend", "css|css3|scss|sass", "Tailwind CSS|HTML"),
    _s("Tailwind CSS", "frontend", "tailwind|tailwindcss|tailwind css", "CSS"),
    _s("React Native", "mobile", "react native|react-native", "React|Flutter"),
    _s("Flutter", "mobile", "flutter", "Dart|React Native"),
    _s("Android", "mobile", "android|android sdk", "Kotlin"),
    _s("iOS", "mobile", "ios|swiftui|uikit", "Swift"),
    # Backend
    _s("Node.js", "backend", "node|node.js|nodejs", "Express|JavaScript"),
    _s("Express", "backend", "express|express.js|expressjs", "Node.js"),
    _s("NestJS", "backend", "nestjs|nest.js", "Node.js"),
    _s("Django", "backend", "django|django rest framework|drf", "Python|FastAPI|Flask"),
    _s("FastAPI", "backend", "fastapi", "Python|Flask|Django"),
    _s("Flask", "backend", "flask", "Python|FastAPI"),
    _s("Spring", "backend", "spring|spring boot|springboot", "Java"),
    _s("Ruby on Rails", "backend", "rails|ruby on rails|ror", "Ruby"),
    _s("Laravel", "backend", "laravel", "PHP"),
    _s(".NET", "backend", ".net|asp.net|dotnet|.net core", "C#"),
    _s("GraphQL", "backend", "graphql|apollo", "REST APIs"),
    _s("REST APIs", "backend", "rest|restful|rest api|rest apis|restful apis", "GraphQL"),
    _s("gRPC", "backend", "grpc|protobuf|protocol buffers", "Microservices"),
    _s("Microservices", "backend", "microservices|microservice architecture", "Distributed Systems"),
    _s("Distributed Systems", "backend", "distributed systems", "Microservices"),
    _s("System Design", "backend", "system design|systems design", "Distributed Systems"),
    # Data stores
    _s("PostgreSQL", "database", "postgres|postgresql|psql", "MySQL|SQL"),
    _s("MySQL", "database", "mysql|mariadb", "PostgreSQL|SQL"),
    _s("SQLite", "database", "sqlite", "SQL"),
    _s("MongoDB", "database", "mongodb|mongo", "NoSQL"),
    _s("Redis", "database", "redis", "Caching"),
    _s("Elasticsearch", "database", "elasticsearch|opensearch|elk", ""),
    _s("Cassandra", "database", "cassandra|scylladb", "NoSQL"),
    _s("DynamoDB", "database", "dynamodb", "NoSQL|AWS"),
    _s("NoSQL", "database", "nosql", "MongoDB"),
    _s("Snowflake", "data", "snowflake", "Data Warehousing"),
    _s("BigQuery", "data", "bigquery", "Data Warehousing|GCP"),
    _s("Data Warehousing", "data", "data warehouse|data warehousing|redshift", "Snowflake|BigQuery"),
    _s("Caching", "backend", "caching|memcached", "Redis"),
    _s("Kafka", "data", "kafka|apache kafka", "Message Queues"),
    _s("Message Queues", "backend", "rabbitmq|sqs|message queue|message queues|pub/sub", "Kafka"),
    # Cloud / DevOps
    _s("AWS", "cloud", "aws|amazon web services|ec2|s3|lambda|aws lambda", "GCP|Azure"),
    _s("GCP", "cloud", "gcp|google cloud|google cloud platform", "AWS|Azure"),
    _s("Azure", "cloud", "azure|microsoft azure", "AWS|GCP"),
    _s("Docker", "devops", "docker|containers|containerization", "Kubernetes"),
    _s("Kubernetes", "devops", "kubernetes|k8s|eks|gke|aks|helm", "Docker"),
    _s("Terraform", "devops", "terraform|infrastructure as code|iac", "AWS"),
    _s("CI/CD", "devops", "ci/cd|cicd|continuous integration|github actions|jenkins|gitlab ci|circleci", ""),
    _s("Linux", "devops", "linux|unix", "Bash"),
    _s("Git", "tools", "git|github|gitlab|bitbucket", ""),
    _s("Observability", "devops", "observability|prometheus|grafana|datadog|opentelemetry", ""),
    _s("Serverless", "cloud", "serverless", "AWS"),
    # Data / ML
    _s("Pandas", "data", "pandas", "NumPy|Python"),
    _s("NumPy", "data", "numpy", "Pandas"),
    _s("Spark", "data", "spark|pyspark|apache spark|databricks", "Hadoop|Scala"),
    _s("Hadoop", "data", "hadoop|hive|hdfs", "Spark"),
    _s("Airflow", "data", "airflow|apache airflow|dagster|prefect", "ETL"),
    _s("ETL", "data", "etl|elt|data pipelines|data pipeline", "Airflow|dbt"),
    _s("dbt", "data", "dbt", "ETL|SQL"),
    _s("Machine Learning", "ml", "machine learning|ml", "Deep Learning|Scikit-learn"),
    _s("Deep Learning", "ml", "deep learning|neural networks", "PyTorch|TensorFlow"),
    _s("PyTorch", "ml", "pytorch|torch", "TensorFlow|Deep Learning"),
    _s("TensorFlow", "ml", "tensorflow|keras", "PyTorch|Deep Learning"),
    _s("Scikit-learn", "ml", "scikit-learn|sklearn|scikit learn", "Machine Learning"),
    _s("NLP", "ml", "nlp|natural language processing", "LLMs"),
    _s("Computer Vision", "ml", "computer vision|opencv|image processing", "Deep Learning"),
    _s("LLMs", "ml", "llm|llms|large language models|generative ai|genai|gpt|prompt engineering", "NLP|RAG"),
    _s(
        "RAG",
        "ml",
        "rag|retrieval augmented generation|retrieval-augmented generation|vector database|vector databases",
        "LLMs",
    ),
    _s("MLOps", "ml", "mlops|mlflow|kubeflow", "Machine Learning"),
    _s("Statistics", "data", "statistics|statistical analysis|hypothesis testing|a/b testing", "R"),
    _s("Data Analysis", "data", "data analysis|data analytics|analytics", "SQL|Excel"),
    _s("Tableau", "data", "tableau", "Power BI"),
    _s("Power BI", "data", "power bi|powerbi", "Tableau"),
    _s("Excel", "tools", "excel|microsoft excel|spreadsheets|vlookup", "Data Analysis"),
    # Testing & practices
    _s("Testing", "practice", "unit testing|integration testing|test automation|tdd|testing", ""),
    _s("Selenium", "testing", "selenium", "Playwright|Testing"),
    _s("Playwright", "testing", "playwright", "Selenium|Cypress"),
    _s("Cypress", "testing", "cypress", "Playwright"),
    _s("Jest", "testing", "jest|vitest", "Testing"),
    _s("Pytest", "testing", "pytest", "Testing|Python"),
    _s("Agile", "practice", "agile|scrum|kanban|sprint planning", "Jira"),
    _s("Jira", "tools", "jira|confluence", "Agile"),
    _s("Security", "practice", "application security|appsec|owasp|security engineering|cybersecurity", ""),
    _s("OAuth", "practice", "oauth|oauth2|openid connect|oidc|sso|saml", "Security"),
    _s("Data Structures & Algorithms", "practice", "data structures|algorithms|dsa", "System Design"),
    _s("OOP", "practice", "oop|object-oriented programming|object oriented programming|object-oriented design", ""),
    # Design & product
    _s("Figma", "design", "figma", "UI Design"),
    _s("UI Design", "design", "ui design|visual design|interface design", "UX Design|Figma"),
    _s("UX Design", "design", "ux design|user experience|ux research|user research|usability testing", "UI Design"),
    _s("Product Management", "product", "product management|product manager|product roadmap|roadmapping", ""),
    _s("Technical Writing", "communication", "technical writing|documentation", ""),
    # Business
    _s("Salesforce", "business", "salesforce|sfdc", "CRM"),
    _s("CRM", "business", "crm|hubspot", "Salesforce"),
    _s("SEO", "marketing", "seo|search engine optimization", "Digital Marketing"),
    _s("Digital Marketing", "marketing", "digital marketing|performance marketing|google ads|meta ads|sem", "SEO"),
    _s("Financial Modeling", "finance", "financial modeling|financial modelling|dcf|valuation", "Excel"),
    _s("Accounting", "finance", "accounting|bookkeeping|gaap|ifrs|tally", ""),
    _s("Stakeholder Management", "soft", "stakeholder management|cross-functional collaboration", "Communication"),
    _s("Communication", "soft", "communication skills|written communication|verbal communication", ""),
    _s("Leadership", "soft", "leadership|team lead|mentoring|mentorship|people management", ""),
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


@lru_cache
def _index() -> tuple[dict[str, SkillDef], dict[str, SkillDef], list[tuple[re.Pattern[str], SkillDef]]]:
    by_name = {_norm(s.name): s for s in SKILLS}
    alias: dict[str, SkillDef] = dict(by_name)
    patterns: list[tuple[re.Pattern[str], SkillDef]] = []
    for s in SKILLS:
        for a in (s.name, *s.aliases):
            if s.case_sensitive_aliases and a == s.name:
                continue  # ambiguous names are matched only via case-sensitive rules below
            alias[_norm(a)] = s
            patterns.append((re.compile(r"(?<![\w+#.])" + re.escape(a) + r"(?![\w+#])", re.IGNORECASE), s))
        for a in s.case_sensitive_aliases:
            # Case-sensitive and not followed by words that make it ambiguous ("Go to", "R&D").
            patterns.append((re.compile(r"(?<![\w+#./-])" + re.escape(a) + r"(?![\w+#&/-]| to\b| ahead\b)"), s))
    # Longer aliases first so "react native" wins over "react".
    patterns.sort(key=lambda p: -len(p[0].pattern))
    return by_name, alias, patterns


def canonicalize(name: str) -> str | None:
    """Map any alias to its canonical skill name; None if unknown to the taxonomy."""
    _, alias, _ = _index()
    hit = alias.get(_norm(name))
    return hit.name if hit else None


def normalize_skill(name: str) -> str:
    """Canonical name when known, otherwise the cleaned original (user-entered skills are kept)."""
    return canonicalize(name) or re.sub(r"\s+", " ", name.strip())[:120]


def get(name: str) -> SkillDef | None:
    by_name, _, _ = _index()
    return by_name.get(_norm(name))


def category_of(name: str) -> str | None:
    s = get(name)
    return s.category if s else None


def related(a: str, b: str) -> bool:
    sa, sb = get(a), get(b)
    if not sa or not sb:
        return False
    return sb.name in sa.related or sa.name in sb.related


def extract_skills(text: str) -> dict[str, list[str]]:
    """Find taxonomy skills in free text. Returns canonical name → evidence snippets."""
    _, _, patterns = _index()
    found: dict[str, list[str]] = {}
    consumed: list[tuple[int, int]] = []
    for pattern, skill in patterns:
        for m in pattern.finditer(text):
            if any(s <= m.start() < e or s < m.end() <= e for s, e in consumed):
                continue
            consumed.append((m.start(), m.end()))
            lo, hi = max(0, m.start() - 60), min(len(text), m.end() + 60)
            snippet = re.sub(r"\s+", " ", text[lo:hi]).strip()
            ev = found.setdefault(skill.name, [])
            if len(ev) < 3:
                ev.append(snippet)
    return found
