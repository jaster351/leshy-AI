from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib import parse

# Целевая библиотека не хранит копии закрытых статей. Леший сохраняет проверяемые
# утверждения, метаданные и URL источника, а полные тексты читает только там, где
# источник разрешает обычный HTTP-доступ. Это сохраняет происхождение знания и
# не превращает LeshyBrain в нелегальную копию научных издательств.
SCIENTIFIC_MATERIAL_TARGET = 2000

# Точный размер закрытых frontier-моделей в параметрах публично не известен.
# Поэтому программа самоизучения задаёт большой практический минимум: миллион
# содержательно различных задач. Они НЕ материализуются в SQLite заранее, а
# детерминированно выдаются по cursor одну за другой. Это не раздувает базу.
SELF_STUDY_TASK_TARGET = 1_000_000

ACADEMIC_DOMAINS = (
    "arxiv.org",
    "pubmed.ncbi.nlm.nih.gov",
    "pmc.ncbi.nlm.nih.gov",
    "nature.com",
    "science.org",
    "plos.org",
    "biorxiv.org",
    "medrxiv.org",
    "jmlr.org",
    "openreview.net",
    "ieeexplore.ieee.org",
    "dl.acm.org",
    "journals.aps.org",
    "frontiersin.org",
    "royalsocietypublishing.org",
    "copernicus.org",
)

SCIENCE_TOPICS = (
    "artificial intelligence foundations and evaluation",
    "large language models transformers alignment and reasoning",
    "retrieval augmented generation information retrieval and provenance",
    "machine learning generalization robustness and uncertainty",
    "reinforcement learning planning and autonomous agents",
    "computer vision multimodal learning and image understanding",
    "speech recognition speech synthesis and audio understanding",
    "robotics control systems and embodied intelligence",
    "cybersecurity defensive security and software supply chain security",
    "distributed systems databases reliability and fault tolerance",
    "programming languages compilers software engineering and testing",
    "human computer interaction accessibility and usable security",
    "statistics causal inference experimental design and reproducibility",
    "mathematics optimization numerical methods and applied probability",
    "quantum physics particle physics and fundamental interactions",
    "condensed matter physics materials and semiconductor science",
    "astrophysics cosmology astronomy and space science",
    "chemistry catalysis electrochemistry and molecular simulation",
    "materials science batteries energy storage and nanomaterials",
    "climate science atmospheric science oceans and Earth systems",
    "energy systems renewable energy grids and energy efficiency",
    "molecular biology cell biology biochemistry and proteomics",
    "genetics genomics epigenetics and evolutionary biology",
    "neuroscience cognition memory learning and brain computer interfaces",
    "psychology decision making behavioral science and social cognition",
    "immunology infectious disease vaccines and host pathogen interactions",
    "epidemiology public health prevention and evidence synthesis",
    "pharmacology drug discovery toxicology and clinical trials methodology",
    "economics econometrics productivity innovation and market design",
    "education science learning science and knowledge retention",
)

# Независимые оси делают задачи содержательно различными. Вместо старых
# 30*16=480 комбинаций используется пространство >1,2 млн комбинаций.
STUDY_LENSES = (
    "foundations terminology and conceptual models",
    "historical development and landmark results",
    "state of the art methods and strongest baselines",
    "measurement metrics and evaluation methodology",
    "failure modes limitations and negative results",
    "robustness uncertainty and out of distribution behavior",
    "efficiency latency memory compute and energy cost",
    "safety security misuse and adversarial resilience",
    "reproducibility replication and research quality",
    "deployment operations monitoring and maintenance",
    "human factors accessibility usability and interaction",
    "ethics governance law standards and accountability",
    "datasets benchmarks data quality and contamination",
    "causal mechanisms interpretation and explainability",
    "applications case studies and real world evidence",
    "open problems future directions and research gaps",
)

EVIDENCE_MODES = (
    "systematic review and meta analysis",
    "review survey and tutorial synthesis",
    "benchmark and comparative evaluation",
    "replication reproducibility and ablation evidence",
    "longitudinal observational and real world evidence",
    "randomized controlled or experimental evidence",
    "standards best practices and consensus guidance",
    "datasets methods protocols and measurement resources",
)

CROSS_DISCIPLINARY_FOCI = (
    "mathematical foundations",
    "software implementation",
    "hardware acceleration",
    "data engineering",
    "information retrieval",
    "human cognition",
    "decision making",
    "privacy",
    "cybersecurity",
    "reliability engineering",
    "economics",
    "education",
    "health and medicine",
    "biology",
    "physics",
    "chemistry",
    "climate and energy",
    "robotics",
    "public policy",
    "open source ecosystems",
)

NEWS_TOPICS = (
    "artificial intelligence and machine learning",
    "cybersecurity vulnerabilities and defensive security",
    "computer hardware semiconductors CPUs GPUs and NPUs",
    "software engineering open source and developer tools",
    "science research discoveries",
    "medicine public health and biotechnology research",
    "space astronomy and physics research",
    "climate energy and environmental science",
    "robotics autonomous systems and human computer interaction",
    "economics technology business and regulation",
)

CURRICULUM_CAPACITY = (
    len(SCIENCE_TOPICS)
    * len(ACADEMIC_DOMAINS)
    * len(STUDY_LENSES)
    * len(EVIDENCE_MODES)
    * len(CROSS_DISCIPLINARY_FOCI)
)
if CURRICULUM_CAPACITY < SELF_STUDY_TASK_TARGET:  # pragma: no cover - защита конфигурации
    raise RuntimeError("Пространство curriculum меньше целевого числа уникальных задач")


@dataclass(frozen=True)
class ScientificQuery:
    topic: str
    domain: str
    query: str
    task_id: int = 0
    lens: str = ""
    evidence_mode: str = ""
    cross_focus: str = ""


def host_of(url: str) -> str:
    try:
        host = (parse.urlparse(str(url)).hostname or "").lower().rstrip(".")
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def is_academic_url(url: str) -> bool:
    host = host_of(url)
    return any(host == domain or host.endswith("." + domain) for domain in ACADEMIC_DOMAINS)


def _curriculum_coordinates(index: int) -> tuple[int, int, int, int, int]:
    """Биективно раскладывает index в координаты curriculum без повторов."""
    value = int(index)
    if value < 0 or value >= SELF_STUDY_TASK_TARGET:
        raise IndexError(f"Задача самоизучения вне диапазона 0..{SELF_STUDY_TASK_TARGET - 1}")

    topic_i, value = value % len(SCIENCE_TOPICS), value // len(SCIENCE_TOPICS)
    domain_i, value = value % len(ACADEMIC_DOMAINS), value // len(ACADEMIC_DOMAINS)
    lens_i, value = value % len(STUDY_LENSES), value // len(STUDY_LENSES)
    evidence_i, value = value % len(EVIDENCE_MODES), value // len(EVIDENCE_MODES)
    focus_i = value % len(CROSS_DISCIPLINARY_FOCI)
    return topic_i, domain_i, lens_i, evidence_i, focus_i


def scientific_query(cursor: int) -> ScientificQuery:
    """Вернуть уникальную задачу автономного самоизучения.

    В старой схеме после 480 запросов комбинации начинали повторяться. Здесь
    каждый cursor от 0 до 999999 отображается в уникальный набор из пяти осей.
    Никакого modulo-wrap в пределах целевого миллиона нет.
    """
    cursor = max(0, int(cursor))
    topic_i, domain_i, lens_i, evidence_i, focus_i = _curriculum_coordinates(cursor)
    topic = SCIENCE_TOPICS[topic_i]
    domain = ACADEMIC_DOMAINS[domain_i]
    lens = STUDY_LENSES[lens_i]
    evidence = EVIDENCE_MODES[evidence_i]
    focus = CROSS_DISCIPLINARY_FOCI[focus_i]
    query = (
        f"{topic}; {lens}; {evidence}; cross-disciplinary focus: {focus}; "
        f"review survey systematic review site:{domain}"
    )
    return ScientificQuery(
        topic=topic,
        domain=domain,
        query=query,
        task_id=cursor,
        lens=lens,
        evidence_mode=evidence,
        cross_focus=focus,
    )


def news_query(cursor: int, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    topic = NEWS_TOPICS[max(0, int(cursor)) % len(NEWS_TOPICS)]
    # Дата делает каждую суточную итерацию отдельной актуальной задачей, а не
    # повтором старой новостной выдачи.
    return f'{topic} latest news research {now:%Y-%m-%d}'


__all__ = [
    "SCIENTIFIC_MATERIAL_TARGET",
    "SELF_STUDY_TASK_TARGET",
    "CURRICULUM_CAPACITY",
    "ACADEMIC_DOMAINS",
    "SCIENCE_TOPICS",
    "STUDY_LENSES",
    "EVIDENCE_MODES",
    "CROSS_DISCIPLINARY_FOCI",
    "NEWS_TOPICS",
    "ScientificQuery",
    "host_of",
    "is_academic_url",
    "scientific_query",
    "news_query",
]
