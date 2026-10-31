from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib import parse

# Целевая библиотека не хранит копии закрытых статей. Леший сохраняет проверяемые
# утверждения, метаданные и URL источника, а полные тексты читает только там, где
# источник разрешает обычный HTTP-доступ. Это сохраняет происхождение знания и
# не превращает LeshyBrain в нелегальную копию научных издательств.
SCIENTIFIC_MATERIAL_TARGET = 2000

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


@dataclass(frozen=True)
class ScientificQuery:
    topic: str
    domain: str
    query: str


def host_of(url: str) -> str:
    try:
        host = (parse.urlparse(str(url)).hostname or "").lower().rstrip(".")
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def is_academic_url(url: str) -> bool:
    host = host_of(url)
    return any(host == domain or host.endswith("." + domain) for domain in ACADEMIC_DOMAINS)


def scientific_query(cursor: int) -> ScientificQuery:
    cursor = max(0, int(cursor))
    topic = SCIENCE_TOPICS[cursor % len(SCIENCE_TOPICS)]
    domain = ACADEMIC_DOMAINS[(cursor // len(SCIENCE_TOPICS)) % len(ACADEMIC_DOMAINS)]
    # Review/survey/systematic review дают Лешему фундаментальную базу, а не набор
    # случайных одиночных публикаций. После полного прохода комбинации повторяются:
    # поисковая выдача со временем меняется и пополняет знания новыми работами.
    query = f'{topic} review survey systematic review site:{domain}'
    return ScientificQuery(topic=topic, domain=domain, query=query)


def news_query(cursor: int, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    topic = NEWS_TOPICS[max(0, int(cursor)) % len(NEWS_TOPICS)]
    # Дата заставляет поисковик предпочитать текущий новостной цикл и не повторять
    # одну и ту же старую выдачу бесконечно.
    return f'{topic} latest news research {now:%Y-%m-%d}'


__all__ = [
    "SCIENTIFIC_MATERIAL_TARGET",
    "ACADEMIC_DOMAINS",
    "SCIENCE_TOPICS",
    "NEWS_TOPICS",
    "ScientificQuery",
    "host_of",
    "is_academic_url",
    "scientific_query",
    "news_query",
]
