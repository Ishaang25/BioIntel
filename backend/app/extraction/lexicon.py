"""Deterministic biomedical pattern library.

This module is *not* a replacement for the language model.  It serves two
purposes:

1. **Backstop recall.**  Regex/gazetteer hits that the model missed are
   surfaced as low-salience candidates, so an obviously-present target or
   endpoint is never silently dropped.
2. **Offline operation.**  The deterministic LLM provider used when no API
   credentials are present relies on these patterns to produce a coherent,
   inspectable analysis, which keeps the whole product demonstrable and the
   integration tests meaningful.

Everything here is intentionally conservative: precision over recall, because
a false entity costs analyst trust.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.enums import ClaimCategory, EntityType, EvidenceTier

# --------------------------------------------------------------- gazetteers ---
MODALITIES: dict[str, str] = {
    "small molecule": "small molecule",
    "monoclonal antibody": "monoclonal antibody",
    "bispecific antibody": "bispecific antibody",
    "antibody-drug conjugate": "antibody-drug conjugate",
    "adc": "antibody-drug conjugate",
    "car-t": "CAR-T cell therapy",
    "car t": "CAR-T cell therapy",
    "tcr-t": "TCR-T cell therapy",
    "crispr": "CRISPR gene editing",
    "base editing": "base editing",
    "prime editing": "prime editing",
    "aav": "AAV gene therapy",
    "lentiviral": "lentiviral gene therapy",
    "mrna": "mRNA therapeutic",
    "sirna": "siRNA",
    "asos": "antisense oligonucleotide",
    "antisense oligonucleotide": "antisense oligonucleotide",
    "oligonucleotide": "oligonucleotide",
    "peptide": "peptide therapeutic",
    "protein degrader": "targeted protein degrader",
    "protac": "PROTAC degrader",
    "molecular glue": "molecular glue",
    "cell therapy": "cell therapy",
    "gene therapy": "gene therapy",
    "vaccine": "vaccine",
    "microbiome": "microbiome therapeutic",
    "radioligand": "radioligand therapy",
    "radiopharmaceutical": "radiopharmaceutical",
    "exosome": "exosome therapeutic",
}

ENDPOINTS: dict[str, str] = {
    "overall survival": "overall survival (OS)",
    "progression-free survival": "progression-free survival (PFS)",
    "objective response rate": "objective response rate (ORR)",
    "orr": "objective response rate (ORR)",
    "complete response": "complete response (CR)",
    "partial response": "partial response (PR)",
    "disease control rate": "disease control rate (DCR)",
    "duration of response": "duration of response (DoR)",
    "event-free survival": "event-free survival (EFS)",
    "hba1c": "HbA1c",
    "egfr slope": "eGFR slope",
    "fev1": "FEV1",
    "adas-cog": "ADAS-Cog",
    "updrs": "UPDRS",
    "mmse": "MMSE",
    "cdr-sb": "CDR-SB",
    "pasi": "PASI",
    "acr20": "ACR20",
    "mayo score": "Mayo score",
    "six-minute walk": "6-minute walk distance",
    "tumor volume": "tumour volume",
    "tumour volume": "tumour volume",
    "viral load": "viral load",
    "seizure frequency": "seizure frequency",
}

BIOMARKERS: dict[str, str] = {
    "ctdna": "circulating tumour DNA (ctDNA)",
    "pd-l1": "PD-L1 expression",
    "msi-h": "microsatellite instability-high",
    "tmb": "tumour mutational burden",
    "amyloid pet": "amyloid PET",
    "p-tau217": "phosphorylated tau 217",
    "p-tau181": "phosphorylated tau 181",
    "nfl": "neurofilament light chain",
    "crp": "C-reactive protein",
    "il-6": "interleukin-6",
    "hba1c": "HbA1c",
    "ldl-c": "LDL cholesterol",
    "troponin": "troponin",
    "psa": "prostate-specific antigen",
    "her2": "HER2 status",
}

MODEL_SYSTEMS: dict[str, str] = {
    "pdx": "patient-derived xenograft",
    "xenograft": "xenograft model",
    "organoid": "organoid model",
    "ipsc": "iPSC-derived model",
    "knock-in": "knock-in model",
    "knockout": "knockout model",
    "mptp": "MPTP model",
    "eae": "EAE model",
    "db/db": "db/db mouse model",
    "nod scid": "NOD/SCID mouse",
    "cynomolgus": "cynomolgus macaque",
    "non-human primate": "non-human primate",
    "zebrafish": "zebrafish model",
    "primary human": "primary human cells",
}

#: High-signal disease terms. Deliberately short; the model handles the tail.
DISEASES: tuple[str, ...] = (
    "alzheimer's disease",
    "alzheimer disease",
    "parkinson's disease",
    "parkinson disease",
    "amyotrophic lateral sclerosis",
    "multiple sclerosis",
    "huntington's disease",
    "type 1 diabetes",
    "type 2 diabetes",
    "obesity",
    "nash",
    "mash",
    "non-alcoholic steatohepatitis",
    "metabolic dysfunction-associated steatohepatitis",
    "chronic kidney disease",
    "idiopathic pulmonary fibrosis",
    "cystic fibrosis",
    "sickle cell disease",
    "beta-thalassemia",
    "duchenne muscular dystrophy",
    "spinal muscular atrophy",
    "rheumatoid arthritis",
    "psoriasis",
    "atopic dermatitis",
    "ulcerative colitis",
    "crohn's disease",
    "inflammatory bowel disease",
    "lupus",
    "systemic lupus erythematosus",
    "heart failure",
    "atherosclerosis",
    "hypertension",
    "pulmonary arterial hypertension",
    "asthma",
    "copd",
    "sepsis",
    "non-small cell lung cancer",
    "small cell lung cancer",
    "pancreatic cancer",
    "pancreatic ductal adenocarcinoma",
    "colorectal cancer",
    "breast cancer",
    "triple-negative breast cancer",
    "prostate cancer",
    "ovarian cancer",
    "glioblastoma",
    "acute myeloid leukemia",
    "acute myeloid leukaemia",
    "multiple myeloma",
    "diffuse large b-cell lymphoma",
    "melanoma",
    "hepatocellular carcinoma",
    "renal cell carcinoma",
    "gastric cancer",
    "solid tumors",
    "solid tumours",
    "influenza",
    "hiv",
    "hepatitis b",
    "tuberculosis",
    "malaria",
    "covid-19",
    "macular degeneration",
    "diabetic retinopathy",
    "glaucoma",
    "depression",
    "schizophrenia",
    "epilepsy",
    "chronic pain",
    "migraine",
    "osteoarthritis",
)

#: Well-known drug targets (gene/protein symbols). Matched case-sensitively as
#: whole tokens to avoid English-word collisions.
KNOWN_TARGETS: tuple[str, ...] = (
    "KRAS",
    "NRAS",
    "HRAS",
    "EGFR",
    "HER2",
    "ERBB2",
    "ALK",
    "ROS1",
    "MET",
    "RET",
    "BRAF",
    "MEK",
    "ERK",
    "PI3K",
    "AKT",
    "MTOR",
    "CDK4",
    "CDK6",
    "CDK7",
    "CDK9",
    "PARP",
    "ATM",
    "ATR",
    "WEE1",
    "BCL2",
    "MCL1",
    "MDM2",
    "TP53",
    "PTEN",
    "BRCA1",
    "BRCA2",
    "IDH1",
    "IDH2",
    "FLT3",
    "JAK1",
    "JAK2",
    "JAK3",
    "TYK2",
    "BTK",
    "SYK",
    "PD1",
    "PDCD1",
    "PDL1",
    "CTLA4",
    "LAG3",
    "TIGIT",
    "TIM3",
    "CD19",
    "CD20",
    "CD3",
    "CD38",
    "BCMA",
    "GPRC5D",
    "CLDN18",
    "TROP2",
    "NECTIN4",
    "DLL3",
    "STING",
    "TLR7",
    "TLR9",
    "CGAS",
    "NLRP3",
    "IL17A",
    "IL23",
    "IL4R",
    "IL5",
    "IL13",
    "IL6R",
    "TNF",
    "TSLP",
    "CCR5",
    "CXCR4",
    "S1P1",
    "SGLT2",
    "GLP1R",
    "GIPR",
    "GCGR",
    "PCSK9",
    "LPA",
    "ANGPTL3",
    "APOC3",
    "HMGCR",
    "SOD1",
    "TDP43",
    "TARDBP",
    "LRRK2",
    "SNCA",
    "GBA1",
    "APP",
    "MAPT",
    "BACE1",
    "TREM2",
    "APOE",
    "HTT",
    "SMN1",
    "SMN2",
    "DMD",
    "CFTR",
    "HBB",
    "F8",
    "F9",
    "TTR",
    "AAT",
    "SERPINA1",
    "PNPLA3",
    "HSD17B13",
    "FGF21",
    "THRB",
    "NLRP1",
    "ROCK2",
    "RIPK1",
    "RIPK2",
    "USP1",
    "POLQ",
    "PRMT5",
    "MAT2A",
    "WRN",
    "SHP2",
    "PTPN11",
    "SOS1",
    "AURKA",
    "PLK1",
    "EZH2",
    "MENIN",
    "KMT2A",
    "NSD2",
    "GSPT1",
    "IKZF1",
    "IKZF3",
    "CRBN",
    "VHL",
    "KEAP1",
    "NRF2",
    "HIF2A",
    "EPAS1",
)

#: Mechanistic verbs that indicate a scientific assertion.
MECHANISM_VERBS: tuple[str, ...] = (
    "inhibits",
    "inhibited",
    "inhibition",
    "activates",
    "activation",
    "agonist",
    "antagonist",
    "degrades",
    "degradation",
    "blocks",
    "blockade",
    "modulates",
    "binds",
    "binding",
    "targets",
    "targeting",
    "silences",
    "knocks down",
    "upregulates",
    "downregulates",
    "suppresses",
    "induces",
    "restores",
    "corrects",
    "edits",
    "editing",
    "reduces",
    "increases",
    "decreases",
    "improves",
    "prevents",
    "rescues",
    "potentiates",
    "stabilizes",
    "stabilises",
    "disrupts",
)

HEDGE_WORDS: tuple[str, ...] = (
    "may ",
    "might ",
    "could ",
    "potential",
    "potentially",
    "designed to",
    "believe",
    "we think",
    "expected to",
    "aims to",
    "hope",
    "should ",
    "promising",
    "suggests",
    "appears to",
    "likely",
    "anticipate",
)

#: Marketing superlatives that are not falsifiable scientific content.
PUFFERY = (
    "first-in-class",
    "best-in-class",
    "revolutionary",
    "breakthrough",
    "paradigm shift",
    "game-changing",
    "unprecedented",
    "transformative",
    "world-class",
    "disruptive",
)

# ------------------------------------------------------------------ regexes ---
QUANT_PATTERNS: dict[str, re.Pattern[str]] = {
    "ic50": re.compile(r"\bIC[_ ]?50\b\s*(?:of|=|:)?\s*([0-9][0-9.,]*)\s*([pnµumM]{0,2}M)?", re.I),
    "ec50": re.compile(r"\bEC[_ ]?50\b\s*(?:of|=|:)?\s*([0-9][0-9.,]*)\s*([pnµumM]{0,2}M)?", re.I),
    "ki": re.compile(r"\bK[id]\b\s*(?:of|=|:)?\s*([0-9][0-9.,]*)\s*([pnµumM]{0,2}M)?"),
    "percent": re.compile(r"\b([0-9]{1,3}(?:\.[0-9]+)?)\s?%"),
    "fold": re.compile(r"\b([0-9]+(?:\.[0-9]+)?)\s?-?\s?fold\b", re.I),
    "pvalue": re.compile(r"\bp\s?[<>=]\s?(0?\.[0-9]+|[0-9](?:\.[0-9]+)?e-?[0-9]+)", re.I),
    "sample_size": re.compile(r"\bn\s?=\s?([0-9]{1,6})", re.I),
    "hazard_ratio": re.compile(r"\bHR\s?[=:]?\s?([0-9]?\.[0-9]+)", re.I),
    "confidence_interval": re.compile(r"\b95%\s?CI[:\s]*\[?([-0-9.,\s]+)\]?", re.I),
    "dose": re.compile(r"\b([0-9]+(?:\.[0-9]+)?)\s?(mg/kg|mg|µg|ug|ng|g)\b"),
}

NCT_RE = re.compile(r"\bNCT\d{8}\b")
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:\w]+\b")
PMID_RE = re.compile(r"\bPMID:?\s?(\d{6,9})\b", re.I)

#: Candidate drug/asset codes such as "NG-101", "ABC1234", "BI 425809".
ASSET_CODE_RE = re.compile(r"\b([A-Z]{2,5})[- ]?(\d{2,5})\b")

_TARGET_TOKEN_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9}(?:-[A-Z0-9]{1,4})?)\b")

#: Tokens that look like gene symbols but are ordinary business abbreviations.
_TARGET_STOPWORDS = frozenset(
    {
        "USA",
        "CEO",
        "CTO",
        "CSO",
        "CFO",
        "COO",
        "MD",
        "PHD",
        "FDA",
        "EMA",
        "IND",
        "NDA",
        "BLA",
        "IPO",
        "ROI",
        "IRR",
        "TAM",
        "SAM",
        "SOM",
        "IP",
        "R&D",
        "AI",
        "ML",
        "API",
        "GMP",
        "CMC",
        "CRO",
        "CDMO",
        "NIH",
        "NCI",
        "WHO",
        "EU",
        "UK",
        "US",
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "FY",
        "KOL",
        "PDF",
        "OK",
        "NEW",
        "THE",
        "AND",
        "FOR",
        "ALL",
        "ANY",
        "KEY",
        "TOP",
        "PER",
        "USD",
        "EUR",
        "PPT",
        "TBD",
        "N/A",
        "CI",
        "SD",
        "SEM",
        "PK",
        "PD",
        "ADME",
        "MOA",
        "POC",
        "IIT",
        "SAE",
        "AE",
    }
)

#: Measurement/statistic abbreviations that superficially look like symbols.
_METRIC_TOKENS = frozenset(
    {
        "IC50",
        "IC90",
        "EC50",
        "EC90",
        "KD",
        "KI",
        "MIC",
        "AUC",
        "CMAX",
        "TMAX",
        "MTD",
        "NOAEL",
        "HR",
        "OR",
        "RR",
        "ORR",
        "PFS",
        "OS",
        "DCR",
        "DOR",
        "CR",
        "PR",
        "SD",
        "TEAE",
        "MAD",
        "SAD",
        "BID",
        "QD",
        "IV",
        "SC",
        "PO",
        "NHP",
        "MPTP",
        "EAE",
        "PDX",
        "IPSC",
        "GLP",
        "GMP",
        "MRI",
        "PET",
        "CT",
        "ELISA",
        "PCR",
        "QPCR",
        "RNA",
        "DNA",
        "MRNA",
        "SIRNA",
        "ASO",
        "AAV",
        "LNP",
        "ADC",
        "CAR",
        "TCR",
        "FDA",
        "EMA",
        "IND",
        "NDA",
        "BLA",
        "CRO",
        "CDMO",
    }
)


@dataclass(slots=True)
class LexiconHit:
    entity_type: EntityType
    surface: str
    canonical: str


def _find_phrases(text: str, table: dict[str, str], entity_type: EntityType) -> list[LexiconHit]:
    lowered = text.lower()
    hits: list[LexiconHit] = []
    seen: set[str] = set()
    for phrase, canonical in table.items():
        if re.search(rf"(?<![\w-]){re.escape(phrase)}(?![\w-])", lowered):
            if canonical in seen:
                continue
            seen.add(canonical)
            hits.append(LexiconHit(entity_type, phrase, canonical))
    return hits


def find_diseases(text: str) -> list[LexiconHit]:
    table = {d: d.title() if not d.isupper() else d for d in DISEASES}
    return _find_phrases(text, table, EntityType.DISEASE)


def find_targets(text: str) -> list[LexiconHit]:
    """Find gene/protein symbols: gazetteer hits plus conservative novel tokens."""
    hits: list[LexiconHit] = []
    seen: set[str] = set()
    for symbol in KNOWN_TARGETS:
        if re.search(rf"(?<![\w-]){re.escape(symbol)}(?![\w])", text, re.IGNORECASE):
            if symbol in seen:
                continue
            seen.add(symbol)
            hits.append(LexiconHit(EntityType.TARGET, symbol, symbol))
    for match in _TARGET_TOKEN_RE.finditer(text):
        token = match.group(1)
        upper = token.upper()
        if token in seen or upper in _TARGET_STOPWORDS or upper in _METRIC_TOKENS:
            continue
        if len(token) < 3 or token.isdigit() or not any(c.isalpha() for c in token):
            continue
        # Require a nearby mechanistic cue so we do not harvest acronyms.
        window = text[max(0, match.start() - 90) : match.end() + 90].lower()
        if any(
            verb in window
            for verb in (
                "inhibit",
                "target",
                "agonis",
                "antagonis",
                "degrad",
                "kinase",
                "receptor",
                "protein",
                "gene",
                "pathway",
                "expression",
                "mutation",
            )
        ):
            seen.add(token)
            hits.append(LexiconHit(EntityType.TARGET, token, token))
    return hits


def find_assets(text: str) -> list[LexiconHit]:
    """Company asset/drug codes such as ``NG-101`` or ``BI 425809``."""
    hits: list[LexiconHit] = []
    seen: set[str] = set()
    for match in ASSET_CODE_RE.finditer(text):
        prefix, number = match.group(1), match.group(2)
        if prefix in _TARGET_STOPWORDS or prefix in _METRIC_TOKENS:
            continue
        if f"{prefix}{number}".upper() in _METRIC_TOKENS:
            continue
        canonical = f"{prefix}-{number}"
        if canonical in seen:
            continue
        seen.add(canonical)
        hits.append(LexiconHit(EntityType.DRUG, match.group(0), canonical))
    return hits


def find_modalities(text: str) -> list[LexiconHit]:
    return _find_phrases(text, MODALITIES, EntityType.MODALITY)


def find_endpoints(text: str) -> list[LexiconHit]:
    return _find_phrases(text, ENDPOINTS, EntityType.ENDPOINT)


def find_biomarkers(text: str) -> list[LexiconHit]:
    return _find_phrases(text, BIOMARKERS, EntityType.BIOMARKER)


def find_model_systems(text: str) -> list[LexiconHit]:
    return _find_phrases(text, MODEL_SYSTEMS, EntityType.MODEL_SYSTEM)


def find_all_entities(text: str) -> list[LexiconHit]:
    """All gazetteer hits, deduplicated with earlier (more specific) types winning."""
    ordered = [
        *find_diseases(text),
        *find_assets(text),
        *find_model_systems(text),
        *find_endpoints(text),
        *find_biomarkers(text),
        *find_modalities(text),
        *find_targets(text),
    ]
    claimed: set[str] = set()
    out: list[LexiconHit] = []
    for hit in ordered:
        key = hit.canonical.lower()
        surface = hit.surface.lower()
        if key in claimed or surface in claimed:
            continue
        claimed.add(key)
        claimed.add(surface)
        out.append(hit)
    return out


def find_quantities(text: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for metric, pattern in QUANT_PATTERNS.items():
        for match in pattern.finditer(text):
            out.append(
                {
                    "metric": metric,
                    "value": match.group(1),
                    "unit": (match.group(2) if match.lastindex and match.lastindex >= 2 else "")
                    or "",
                    "span": match.group(0),
                }
            )
    return out


def infer_evidence_tier(text: str) -> EvidenceTier:
    """Infer the strongest evidence tier a passage describes."""
    lowered = text.lower()
    checks: list[tuple[EvidenceTier, tuple[str, ...]]] = [
        (EvidenceTier.APPROVED, ("fda approved", "ema approved", "marketing authorization")),
        (
            EvidenceTier.CLINICAL_PHASE_3,
            ("phase 3", "phase iii", "pivotal trial", "registrational"),
        ),
        (EvidenceTier.CLINICAL_PHASE_2, ("phase 2", "phase ii", "proof-of-concept trial")),
        (
            EvidenceTier.CLINICAL_PHASE_1,
            ("phase 1", "phase i ", "first-in-human", "healthy volunteers", "sad/mad"),
        ),
        (
            EvidenceTier.IN_VIVO_ANIMAL,
            (
                "mouse",
                "mice",
                "rat",
                "murine",
                "in vivo",
                "xenograft",
                "primate",
                "macaque",
                "canine",
                "zebrafish",
            ),
        ),
        (
            EvidenceTier.EX_VIVO_HUMAN,
            ("patient-derived", "primary human", "human organoid", "ex vivo", "biopsy"),
        ),
        (
            EvidenceTier.IN_VITRO,
            ("in vitro", "cell line", "cells were", "assay", "biochemical", "ic50", "ec50"),
        ),
        (
            EvidenceTier.IN_SILICO,
            (
                "in silico",
                "computational",
                "molecular dynamics",
                "docking",
                "machine learning",
                "predicted structure",
                "alphafold",
            ),
        ),
        (EvidenceTier.LITERATURE_ONLY, ("published", "literature", "et al", "peer-reviewed")),
    ]
    for tier, needles in checks:
        if any(needle in lowered for needle in needles):
            return tier
    return EvidenceTier.NONE_STATED


def infer_claim_category(text: str) -> ClaimCategory:
    lowered = text.lower()
    rules: list[tuple[ClaimCategory, tuple[str, ...]]] = [
        (
            ClaimCategory.CLINICAL_EFFICACY,
            (
                "phase 1",
                "phase 2",
                "phase 3",
                "phase i",
                "phase ii",
                "phase iii",
                "patients",
                "clinical trial",
                "orr",
                "overall survival",
                "progression-free",
            ),
        ),
        (
            ClaimCategory.SAFETY,
            (
                "safety",
                "tolerab",
                "adverse event",
                "toxicity",
                "no serious",
                "side effect",
                "therapeutic index",
                "off-target",
            ),
        ),
        (
            ClaimCategory.PRECLINICAL_EFFICACY,
            (
                "mouse",
                "mice",
                "in vivo",
                "xenograft",
                "tumor volume",
                "tumour volume",
                "animal model",
                "efficacy in",
            ),
        ),
        (
            ClaimCategory.MECHANISM,
            (
                "mechanism",
                "inhibits",
                "binds",
                "pathway",
                "degrades",
                "agonist",
                "antagonist",
                "selectivity",
                "potency",
                "ic50",
                "ec50",
                "kd",
            ),
        ),
        (
            ClaimCategory.TARGET_VALIDATION,
            (
                "genetically validated",
                "target validation",
                "human genetics",
                "gwas",
                "knockout",
                "loss-of-function",
            ),
        ),
        (
            ClaimCategory.BIOMARKER,
            ("biomarker", "patient selection", "companion diagnostic", "ctdna"),
        ),
        (
            ClaimCategory.MANUFACTURING,
            ("manufactur", "cmc", "gmp", "scale-up", "cost of goods", "yield"),
        ),
        (
            ClaimCategory.REGULATORY,
            (
                "fda",
                "ema",
                "ind",
                "orphan",
                "fast track",
                "breakthrough designation",
                "regulatory",
                "rmat",
            ),
        ),
        (
            ClaimCategory.IP,
            ("patent", "intellectual property", "composition of matter", "freedom to operate"),
        ),
        (ClaimCategory.PLATFORM, ("platform", "pipeline of", "modular", "our technology enables")),
        (ClaimCategory.MARKET, ("market", "tam", "$", "billion", "pricing", "reimbursement")),
        (
            ClaimCategory.COMPETITIVE,
            ("competitor", "versus", "differentiat", "landscape", "unlike"),
        ),
    ]
    for category, needles in rules:
        if any(needle in lowered for needle in needles):
            return category
    return ClaimCategory.OTHER


def has_hedging(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in HEDGE_WORDS)


def has_puffery(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in PUFFERY)


def looks_scientific(text: str) -> bool:
    lowered = text.lower()
    if any(verb in lowered for verb in MECHANISM_VERBS):
        return True
    if find_quantities(text):
        return True
    return bool(find_all_entities(text))
