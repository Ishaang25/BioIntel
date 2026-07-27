"""Entity normalisation and alias expansion for retrieval.

Retrieval false negatives were a major contributor to the Moderna failure.
A deck says "mRNA-1273"; the literature says "Spikevax", "elasomeran", or
"the Moderna COVID-19 vaccine".  A query built from the deck's wording alone
finds nothing, the claim is filed as uncorroborated, and the score drops --
for a reason that has nothing to do with the science.

This module expands a surface form into the family of names the literature
actually uses:

* **drugs** — internal code ↔ INN/generic ↔ brand ↔ partner code;
* **genes/targets** — official symbol ↔ historic aliases ↔ protein names;
* **companies** — legal entity ↔ trading name ↔ subsidiary;
* **mechanisms** — the phrasings a paper would use for the same action;
* **modalities** — "ADC" ↔ "antibody-drug conjugate";
* **indications** — abbreviations ↔ MeSH-style names.

The tables are curated rather than exhaustive: precision matters more than
coverage, because a wrong alias silently retrieves evidence about a different
molecule, which is worse than retrieving nothing.  Structural rules
(``mRNA-1273`` → ``mRNA1273``, ``mRNA 1273``) generalise beyond the tables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.utils.text import collapse_whitespace, normalize_entity_key

# ------------------------------------------------------------------ drugs ---
#: Curated code ↔ INN ↔ brand families. Each tuple is one molecule.
DRUG_ALIAS_FAMILIES: tuple[tuple[str, ...], ...] = (
    # Moderna
    ("mRNA-1273", "elasomeran", "Spikevax", "Moderna COVID-19 vaccine"),
    ("mRNA-1283", "Spikevax next generation", "next-generation COVID-19 mRNA vaccine"),
    ("mRNA-1345", "mResvia", "mRESVIA", "RSV mRNA vaccine"),
    ("mRNA-1647", "CMV mRNA vaccine", "cytomegalovirus mRNA vaccine"),
    ("mRNA-1010", "seasonal influenza mRNA vaccine"),
    ("mRNA-4157", "V940", "intismeran autogene", "individualised neoantigen therapy"),
    ("mRNA-3927", "propionic acidemia mRNA therapy"),
    ("mRNA-3692", "VX-522", "inhaled CFTR mRNA"),
    ("mRNA-1189", "EBV vaccine"),
    ("mRNA-1195", "EBV sequelae vaccine"),
    # Comparators frequently named in decks
    ("pembrolizumab", "Keytruda", "MK-3475", "anti-PD-1"),
    ("nivolumab", "Opdivo", "BMS-936558"),
    ("atezolizumab", "Tecentriq", "MPDL3280A"),
    ("BNT162b2", "tozinameran", "Comirnaty", "Pfizer-BioNTech COVID-19 vaccine"),
    ("AS01", "Arexvy", "GSK RSV vaccine", "RSVPreF3"),
    ("Abrysvo", "RSVpreF", "Pfizer RSV vaccine"),
    ("sotorasib", "AMG 510", "Lumakras"),
    ("adagrasib", "MRTX849", "Krazati"),
    ("semaglutide", "Ozempic", "Wegovy", "NN9535"),
    ("tirzepatide", "Mounjaro", "Zepbound", "LY3298176"),
    ("lecanemab", "Leqembi", "BAN2401"),
    ("donanemab", "Kisunla", "LY3002813"),
    ("exagamglogene autotemcel", "exa-cel", "Casgevy", "CTX001"),
    ("nusinersen", "Spinraza", "ISIS-SMNRx"),
    ("patisiran", "Onpattro", "ALN-TTR02"),
    ("DNL201", "BIIB122", "LRRK2 inhibitor"),
)

# ------------------------------------------------------------------ genes ---
#: Official symbol → aliases seen in the literature.
GENE_ALIASES: dict[str, tuple[str, ...]] = {
    "KRAS": ("K-ras", "KRAS2", "Ki-ras", "c-K-ras"),
    "ERBB2": ("HER2", "HER-2", "neu", "HER2/neu", "CD340"),
    "EGFR": ("HER1", "ERBB1", "epidermal growth factor receptor"),
    "PDCD1": ("PD-1", "PD1", "CD279"),
    "CD274": ("PD-L1", "PDL1", "B7-H1"),
    "CTLA4": ("CTLA-4", "CD152"),
    "LRRK2": ("dardarin", "PARK8"),
    "SNCA": ("alpha-synuclein", "α-synuclein", "PARK1"),
    "MAPT": ("tau", "tau protein", "microtubule-associated protein tau"),
    "APP": ("amyloid precursor protein", "beta-amyloid precursor protein"),
    "TARDBP": ("TDP-43", "TDP43"),
    "SOD1": ("superoxide dismutase 1", "ALS1"),
    "HTT": ("huntingtin", "IT15"),
    "CFTR": ("cystic fibrosis transmembrane conductance regulator", "ABCC7"),
    "HBB": ("beta-globin", "haemoglobin subunit beta"),
    "TTR": ("transthyretin", "prealbumin"),
    "PCSK9": ("proprotein convertase subtilisin/kexin type 9", "NARC-1"),
    "GLP1R": ("GLP-1 receptor", "glucagon-like peptide 1 receptor"),
    "TNF": ("TNF-alpha", "TNFA", "tumour necrosis factor"),
    "IL6R": ("IL-6 receptor", "interleukin-6 receptor", "CD126"),
    "PTPN11": ("SHP2", "SHP-2", "PTP2C"),
    "BRAF": ("B-Raf", "BRAF1", "v-raf murine sarcoma"),
    "ALK": ("anaplastic lymphoma kinase", "CD246"),
    "MET": ("c-Met", "HGFR", "hepatocyte growth factor receptor"),
    "TNFRSF17": ("BCMA", "CD269"),
    "MS4A1": ("CD20", "B1"),
    "TACSTD2": ("TROP2", "TROP-2", "GA733-1"),
    "PCCA": ("propionyl-CoA carboxylase alpha", "propionyl CoA carboxylase subunit alpha"),
    "PCCB": ("propionyl-CoA carboxylase beta",),
    "RAB10": ("p-Rab10", "phospho-Rab10", "Ras-related protein Rab-10"),
}

# -------------------------------------------------------------- companies ---
COMPANY_ALIASES: dict[str, tuple[str, ...]] = {
    "moderna": ("Moderna Inc", "ModernaTX", "Moderna Therapeutics", "MODERNATX INC"),
    "pfizer": ("Pfizer Inc", "Pfizer Laboratories", "Wyeth"),
    "biontech": ("BioNTech SE", "BioNTech RNA Pharmaceuticals"),
    "merck": ("Merck Sharp & Dohme", "MSD", "Merck & Co", "MERCK SHARP DOHME"),
    "gsk": ("GlaxoSmithKline", "Glaxo", "GSK plc"),
    "astrazeneca": ("AstraZeneca", "MedImmune"),
    "roche": ("Hoffmann-La Roche", "Genentech", "Roche Holding"),
    "novartis": ("Novartis Pharmaceuticals", "Sandoz"),
    "sanofi": ("Sanofi-Aventis", "Genzyme"),
    "vertex": ("Vertex Pharmaceuticals",),
    "regeneron": ("Regeneron Pharmaceuticals",),
    "biogen": ("Biogen Idec", "Biogen Inc"),
    "denali": ("Denali Therapeutics",),
    "intellia": ("Intellia Therapeutics",),
    "beam": ("Beam Therapeutics",),
    "crispr therapeutics": ("CRISPR Therapeutics AG",),
    "recursion": ("Recursion Pharmaceuticals",),
    "alnylam": ("Alnylam Pharmaceuticals",),
    "bristol myers squibb": ("BMS", "Bristol-Myers Squibb", "Celgene"),
    "eli lilly": ("Lilly", "Eli Lilly and Company"),
    "novo nordisk": ("Novo Nordisk A/S",),
}

# ------------------------------------------------------------- mechanisms ---
#: A mechanism as a deck writes it → how a paper would phrase it.
MECHANISM_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "kinase inhibitor": (
        "kinase inhibition",
        "protein kinase inhibitor",
        "ATP-competitive inhibitor",
    ),
    "allosteric inhibitor": (
        "allosteric inhibition",
        "allosteric modulator",
        "non-ATP-competitive",
    ),
    "checkpoint inhibitor": ("immune checkpoint blockade", "checkpoint blockade", "immunotherapy"),
    "protein degrader": ("targeted protein degradation", "PROTAC", "molecular glue degrader"),
    "gene editing": ("CRISPR-Cas9", "genome editing", "base editing", "prime editing"),
    "gene therapy": ("AAV gene transfer", "gene transfer", "viral vector gene therapy"),
    "mrna therapeutic": ("messenger RNA therapy", "mRNA-based therapy", "in vivo mRNA delivery"),
    "mrna vaccine": (
        "messenger RNA vaccine",
        "nucleoside-modified mRNA vaccine",
        "lipid nanoparticle vaccine",
    ),
    "antisense": ("antisense oligonucleotide", "ASO", "splice-switching oligonucleotide"),
    "sirna": ("small interfering RNA", "RNA interference", "RNAi therapeutic"),
    "car-t": ("chimeric antigen receptor T cell", "CAR T-cell therapy", "adoptive cell therapy"),
    "adc": ("antibody-drug conjugate", "targeted cytotoxic payload"),
    "bispecific": ("bispecific antibody", "T-cell engager", "BiTE"),
    "neoantigen vaccine": (
        "individualised neoantigen therapy",
        "personalised cancer vaccine",
        "tumour neoantigen",
    ),
    "lnp": ("lipid nanoparticle", "LNP delivery", "ionisable lipid"),
    "agonist": ("receptor agonist", "receptor activation"),
    "antagonist": ("receptor antagonist", "receptor blockade"),
}

# ------------------------------------------------------------ indications ---
INDICATION_ALIASES: dict[str, tuple[str, ...]] = {
    "nsclc": ("non-small cell lung cancer", "non small cell lung carcinoma"),
    "sclc": ("small cell lung cancer",),
    "tnbc": ("triple-negative breast cancer",),
    "aml": ("acute myeloid leukemia", "acute myeloid leukaemia"),
    "all": ("acute lymphoblastic leukemia",),
    "cll": ("chronic lymphocytic leukemia",),
    "dlbcl": ("diffuse large B-cell lymphoma",),
    "mm": ("multiple myeloma",),
    "pdac": ("pancreatic ductal adenocarcinoma", "pancreatic cancer"),
    "hcc": ("hepatocellular carcinoma", "liver cancer"),
    "rcc": ("renal cell carcinoma", "kidney cancer"),
    "gbm": ("glioblastoma", "glioblastoma multiforme"),
    "ad": ("Alzheimer disease", "Alzheimer's disease"),
    "pd": ("Parkinson disease", "Parkinson's disease"),
    "als": ("amyotrophic lateral sclerosis", "motor neuron disease"),
    "ms": ("multiple sclerosis",),
    "dmd": ("Duchenne muscular dystrophy",),
    "sma": ("spinal muscular atrophy",),
    "cf": ("cystic fibrosis",),
    "scd": ("sickle cell disease", "sickle cell anaemia"),
    "nash": (
        "non-alcoholic steatohepatitis",
        "metabolic dysfunction-associated steatohepatitis",
        "MASH",
    ),
    "mash": (
        "metabolic dysfunction-associated steatohepatitis",
        "non-alcoholic steatohepatitis",
        "NASH",
    ),
    "ckd": ("chronic kidney disease",),
    "ipf": ("idiopathic pulmonary fibrosis",),
    "copd": ("chronic obstructive pulmonary disease",),
    "rsv": ("respiratory syncytial virus",),
    "cmv": ("cytomegalovirus",),
    "ebv": ("Epstein-Barr virus", "Epstein Barr virus"),
    "hbv": ("hepatitis B virus",),
    "pah": ("pulmonary arterial hypertension",),
    "ra": ("rheumatoid arthritis",),
    "uc": ("ulcerative colitis",),
    "ibd": ("inflammatory bowel disease",),
    "sle": ("systemic lupus erythematosus", "lupus"),
    "pa": ("propionic acidemia", "propionic acidaemia"),
    "mma": ("methylmalonic acidemia", "methylmalonic acidaemia"),
    "pku": ("phenylketonuria",),
}


@dataclass(slots=True)
class NormalizedEntity:
    """A surface form resolved to a canonical name plus its search aliases."""

    surface: str
    canonical: str
    aliases: list[str] = field(default_factory=list)
    kind: str = "unknown"

    @property
    def search_terms(self) -> list[str]:
        """All forms worth searching, canonical first, deduplicated."""
        return _dedupe([self.canonical, self.surface, *self.aliases])


# --------------------------------------------------------- lookup indices ---
def _build_drug_index() -> dict[str, tuple[str, tuple[str, ...]]]:
    index: dict[str, tuple[str, tuple[str, ...]]] = {}
    for family in DRUG_ALIAS_FAMILIES:
        canonical = family[0]
        for member in family:
            index[normalize_entity_key(member)] = (canonical, family)
    return index


def _build_gene_index() -> dict[str, tuple[str, tuple[str, ...]]]:
    index: dict[str, tuple[str, tuple[str, ...]]] = {}
    for symbol, aliases in GENE_ALIASES.items():
        family = (symbol, *aliases)
        for member in family:
            index[normalize_entity_key(member)] = (symbol, family)
    return index


def _build_company_index() -> dict[str, tuple[str, tuple[str, ...]]]:
    index: dict[str, tuple[str, tuple[str, ...]]] = {}
    for canonical, aliases in COMPANY_ALIASES.items():
        family = (canonical, *aliases)
        for member in family:
            index[normalize_entity_key(member)] = (canonical, family)
    return index


_DRUG_INDEX = _build_drug_index()
_GENE_INDEX = _build_gene_index()
_COMPANY_INDEX = _build_company_index()

#: Codes like "mRNA-1273", "BNT162b2", "VX-522", "AMG 510".
_ASSET_CODE_RE = re.compile(r"^([A-Za-z]{2,6})[\s\-_]?(\d{2,6}[A-Za-z]?\d?)$")


def _structural_variants(name: str) -> list[str]:
    """Punctuation variants of an asset code that indexes spell differently."""
    cleaned = collapse_whitespace(name)
    match = _ASSET_CODE_RE.match(cleaned)
    if not match:
        return []
    prefix, number = match.group(1), match.group(2)
    return _dedupe(
        [f"{prefix}-{number}", f"{prefix} {number}", f"{prefix}{number}"],
        exclude={cleaned},
    )


# -------------------------------------------------------------- public API ---
def normalize_drug(name: str) -> NormalizedEntity:
    key = normalize_entity_key(name)
    if key in _DRUG_INDEX:
        canonical, family = _DRUG_INDEX[key]
        aliases = [m for m in family if normalize_entity_key(m) != normalize_entity_key(canonical)]
        return NormalizedEntity(name, canonical, _dedupe(aliases), "drug")
    return NormalizedEntity(name, name, _structural_variants(name), "drug")


def normalize_gene(name: str) -> NormalizedEntity:
    key = normalize_entity_key(name)
    if key in _GENE_INDEX:
        symbol, family = _GENE_INDEX[key]
        aliases = [m for m in family if normalize_entity_key(m) != normalize_entity_key(symbol)]
        return NormalizedEntity(name, symbol, _dedupe(aliases), "target")
    return NormalizedEntity(name, name.upper() if name.isupper() else name, [], "target")


def normalize_company(name: str) -> NormalizedEntity:
    key = normalize_entity_key(name)
    if key in _COMPANY_INDEX:
        canonical, family = _COMPANY_INDEX[key]
        aliases = [m for m in family if normalize_entity_key(m) != normalize_entity_key(canonical)]
        return NormalizedEntity(name, canonical, _dedupe(aliases), "company")
    # Strip the corporate suffix so "NeuroGen Therapeutics, Inc." also matches
    # "NeuroGen Therapeutics" and "NeuroGen".
    stripped = re.sub(
        r"\b(inc|llc|ltd|limited|corp|corporation|plc|ag|sa|nv|se|gmbh|co|company|"
        r"therapeutics|pharmaceuticals|pharma|biosciences|bio|holdings)\b\.?",
        "",
        name,
        flags=re.IGNORECASE,
    )
    stripped = collapse_whitespace(stripped.replace(",", " ")).strip()
    aliases = [stripped] if stripped and normalize_entity_key(stripped) != key else []
    return NormalizedEntity(name, name, aliases, "company")


def normalize_indication(name: str) -> NormalizedEntity:
    key = collapse_whitespace(name).lower().strip()
    if key in INDICATION_ALIASES:
        aliases = list(INDICATION_ALIASES[key])
        return NormalizedEntity(name, aliases[0], _dedupe([name, *aliases[1:]]), "disease")
    # Reverse direction: a full name whose abbreviation is well known.
    for abbreviation, expansions in INDICATION_ALIASES.items():
        if any(key == expansion.lower() for expansion in expansions):
            others = [e for e in expansions if e.lower() != key]
            return NormalizedEntity(name, name, _dedupe([abbreviation.upper(), *others]), "disease")
    return NormalizedEntity(name, name, [], "disease")


def expand_mechanism(text: str) -> list[str]:
    """Literature phrasings for a mechanism as a deck describes it."""
    lowered = collapse_whitespace(text).lower()
    out: list[str] = []
    for phrase, expansions in MECHANISM_EXPANSIONS.items():
        if phrase in lowered:
            out.extend(expansions)
    return _dedupe(out)


def normalize_entity(name: str, kind: str) -> NormalizedEntity:
    """Dispatch to the right normaliser for an entity ``kind``."""
    return {
        "drug": normalize_drug,
        "target": normalize_gene,
        "gene": normalize_gene,
        "company": normalize_company,
        "disease": normalize_indication,
        "indication": normalize_indication,
    }.get(kind, lambda n: NormalizedEntity(n, n, [], kind))(name)


def expand_query_terms(names: list[str], *, kind: str = "unknown", limit: int = 12) -> list[str]:
    """Every search term worth trying for ``names``.

    Used by the query planner so a search for "mRNA-1345" also reaches
    "mRESVIA" and "RSV mRNA vaccine".
    """
    terms: list[str] = []
    for name in names:
        cleaned = collapse_whitespace(name)
        if not cleaned:
            continue
        if kind == "unknown":
            # Try every table; a curated hit in any of them is trustworthy.
            for normaliser in (normalize_drug, normalize_gene, normalize_indication):
                resolved = normaliser(cleaned)
                if resolved.aliases:
                    terms.extend(resolved.search_terms)
                    break
            else:
                terms.append(cleaned)
                terms.extend(_structural_variants(cleaned))
        else:
            terms.extend(normalize_entity(cleaned, kind).search_terms)
    return _dedupe(terms)[:limit]


def build_or_clause(terms: list[str], *, field: str | None = None) -> str:
    """A PubMed OR clause over alias variants.

    ``build_or_clause(["mRNA-1345", "mRESVIA"], field="tiab")`` yields
    ``("mRNA-1345"[tiab] OR "mRESVIA"[tiab])``.
    """
    usable = [t for t in _dedupe(terms) if t.strip()]
    if not usable:
        return ""
    suffix = f"[{field}]" if field else ""
    quoted = [f'"{t}"{suffix}' for t in usable]
    if len(quoted) == 1:
        return quoted[0]
    return "(" + " OR ".join(quoted) + ")"


def _dedupe(values: list[str], *, exclude: set[str] | None = None) -> list[str]:
    excluded = {normalize_entity_key(e) for e in (exclude or set())}
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = collapse_whitespace(value)
        key = normalize_entity_key(cleaned)
        if not key or key in seen or key in excluded:
            continue
        seen.add(key)
        out.append(cleaned)
    return out
