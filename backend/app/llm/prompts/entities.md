Identify the scientific entities named in this biotech pitch deck.

## Source material

$pages

## Entity types

- `disease` — indications and conditions (e.g. "pancreatic ductal adenocarcinoma")
- `target` — molecular targets: genes, proteins, receptors, enzymes (e.g. "KRAS G12C", "LRRK2")
- `drug` — named therapeutic agents, including the company's own asset codes (e.g. "NG-101", "pembrolizumab")
- `biomarker` — measurable indicators used for selection, stratification or response (e.g. "ctDNA", "p-tau217")
- `mechanism` — the mechanistic action claimed (e.g. "allosteric LRRK2 kinase inhibition")
- `modality` — the therapeutic format (e.g. "antibody-drug conjugate", "AAV gene therapy")
- `endpoint` — clinical or preclinical measures (e.g. "progression-free survival", "tumour volume")
- `assay` — named experimental assays (e.g. "TR-FRET binding assay")
- `model_system` — experimental systems (e.g. "MPTP mouse model", "patient-derived organoids")
- `pathway` — biological pathways (e.g. "cGAS-STING")
- `company` — the subject company and any named commercial third parties
- `institution` — universities, hospitals, research institutes

## Rules

- **Deduplicate.** "K-ras", "KRAS" and "KRAS G12C" describing the same target
  become one entity, with the others as aliases. Choose as `name` the form the
  deck uses most prominently.
- **`canonical_name`** — supply the standard scientific name only when you are
  confident (e.g. `HER2` for "Her-2/neu"). Otherwise `null`. Never fabricate an
  identifier, accession or database ID.
- **`role_in_program`** — how the *deck* says this entity relates to the
  company's programme. If the deck does not say, use `null`. Do not supply
  textbook background as the role.
- **`description`** — one factual sentence. If you are not confident of the
  entity's identity, use `null` rather than guessing.
- **`source_pages`** — every page the entity appears on.
- **`confidence`** — lower it for ambiguous acronyms. Many three-letter strings
  in a deck are business abbreviations, not gene symbols; do not classify a
  token as a target unless the surrounding text supports it.
- Do not include an entity that only appears in a logo, a footer, or a legal
  disclaimer.
