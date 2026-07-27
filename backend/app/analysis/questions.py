"""Stage 9: risk register and diligence questions.

The model generates the narrative risks and the questions; the deterministic
rules from :mod:`app.analysis.rules` are merged in so guaranteed findings can
never be omitted.  Where a model risk restates a rule finding, the rule wins --
it carries an auditable trigger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from app.analysis.rules import RuleFinding
from app.core.config import settings
from app.core.enums import QuestionPriority, RiskCategory, RiskSeverity
from app.core.logging import get_logger
from app.llm import prompts
from app.llm.client import LLMClient
from app.llm.schemas import RisksAndQuestionsOut
from app.utils.text import normalize_for_match, truncate

log = get_logger(__name__)

#: Model risks matching a rule finding above this are considered duplicates.
RISK_DEDUPE_THRESHOLD = 82.0
MAX_QUESTIONS = 15
MAX_RISKS = 20
#: A rule firing for at least this many claims is collapsed into one entry.
AGGREGATE_RULE_AT = 3


@dataclass(slots=True)
class Risk:
    title: str
    category: RiskCategory
    severity: RiskSeverity
    description: str
    claim_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    source_pages: list[int] = field(default_factory=list)
    is_rule_based: bool = False
    rule_id: str | None = None


@dataclass(slots=True)
class Question:
    question: str
    rationale: str
    priority: QuestionPriority
    category: RiskCategory
    what_good_looks_like: str
    claim_ids: list[str] = field(default_factory=list)
    rank: int = 0


@dataclass(slots=True)
class RiskQuestionResult:
    risks: list[Risk] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)

    def metrics(self) -> dict[str, Any]:
        return {
            "risks": len(self.risks),
            "rule_based_risks": sum(1 for r in self.risks if r.is_rule_based),
            "questions": len(self.questions),
            "critical_questions": sum(
                1 for q in self.questions if q.priority is QuestionPriority.CRITICAL
            ),
        }


class RiskQuestionStage:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def run(
        self,
        *,
        company_context: str,
        claim_summaries: list[dict[str, Any]],
        rule_findings: list[RuleFinding],
        ref_to_claim_id: dict[str, str],
    ) -> RiskQuestionResult:
        output = await self.llm.structured(
            purpose="risks_questions",
            stage="questions",
            system=prompts.system(),
            user=prompts.render(
                "risks_questions",
                company_context=company_context or "(no company profile could be extracted)",
                claims=_format_claims(claim_summaries),
                signals=_format_signals(rule_findings),
            ),
            schema=RisksAndQuestionsOut,
            model=settings.model_reasoning,
            context={"claims": claim_summaries},
        )

        rule_risks = _aggregate_rule_findings(rule_findings)
        model_risks = [
            Risk(
                title=item.title,
                category=item.category,
                severity=item.severity,
                description=item.description,
                claim_ids=_resolve_refs(item.related_claim_refs, ref_to_claim_id),
            )
            for item in output.risks
        ]
        risks = _merge_risks(rule_risks, model_risks)

        questions = [
            Question(
                question=item.question,
                rationale=item.rationale,
                priority=item.priority,
                category=item.category,
                what_good_looks_like=item.what_good_looks_like,
                claim_ids=_resolve_refs(item.related_claim_refs, ref_to_claim_id),
            )
            for item in output.questions
        ]
        questions = _rank_questions(questions)

        result = RiskQuestionResult(risks=risks[:MAX_RISKS], questions=questions[:MAX_QUESTIONS])
        log.info("questions.completed", **result.metrics())
        return result


# ------------------------------------------------------------------ merging ---
def _aggregate_rule_findings(findings: list[RuleFinding]) -> list[Risk]:
    """Collapse a rule that fired across many claims into one register entry.

    Six separate "effect reported without sample size" rows push more serious
    findings off the register and read as noise.  One entry that names the six
    claims is both shorter and more useful -- and it means a medium-severity
    pattern is never crowded out by higher-severity individual findings.
    """
    grouped: dict[str, list[RuleFinding]] = {}
    for finding in findings:
        grouped.setdefault(finding.rule_id, []).append(finding)

    risks: list[Risk] = []
    for rule_id, group in grouped.items():
        if len(group) < AGGREGATE_RULE_AT:
            risks.extend(_from_rule(f) for f in group)
            continue

        first = group[0]
        claim_ids = [f.claim_id for f in group if f.claim_id]
        pages = sorted({p for f in group for p in f.source_pages})
        risks.append(
            Risk(
                title=f"{first.title} ({len(group)} claims)",
                category=first.category,
                severity=first.severity,
                description=(
                    f"This pattern was detected on {len(group)} separate claims"
                    + (f" (pages {', '.join(str(p) for p in pages)})" if pages else "")
                    + ". Example: "
                    + truncate(first.description, 400)
                ),
                claim_ids=claim_ids[:12],
                evidence_ids=[e for f in group for e in f.evidence_ids][:10],
                source_pages=pages,
                is_rule_based=True,
                rule_id=rule_id,
            )
        )
    return risks


def _from_rule(finding: RuleFinding) -> Risk:
    return Risk(
        title=finding.title,
        category=finding.category,
        severity=finding.severity,
        description=finding.description,
        claim_ids=[finding.claim_id] if finding.claim_id else [],
        evidence_ids=list(finding.evidence_ids),
        source_pages=list(finding.source_pages),
        is_rule_based=True,
        rule_id=finding.rule_id,
    )


def _merge_risks(rule_risks: list[Risk], model_risks: list[Risk]) -> list[Risk]:
    """Rule findings take precedence; model risks fill gaps they do not cover."""
    merged = list(rule_risks)
    rule_keys = [normalize_for_match(f"{r.title} {r.description[:160]}") for r in rule_risks]

    for risk in model_risks:
        key = normalize_for_match(f"{risk.title} {risk.description[:160]}")
        if any(
            fuzz.token_set_ratio(key, existing) >= RISK_DEDUPE_THRESHOLD for existing in rule_keys
        ):
            continue
        merged.append(risk)
        rule_keys.append(key)

    severity_order = {
        RiskSeverity.CRITICAL: 0,
        RiskSeverity.HIGH: 1,
        RiskSeverity.MEDIUM: 2,
        RiskSeverity.LOW: 3,
        RiskSeverity.INFO: 4,
    }
    merged.sort(key=lambda r: (severity_order.get(r.severity, 5), not r.is_rule_based))
    return merged


def _rank_questions(questions: list[Question]) -> list[Question]:
    priority_order = {
        QuestionPriority.CRITICAL: 0,
        QuestionPriority.HIGH: 1,
        QuestionPriority.MEDIUM: 2,
        QuestionPriority.LOW: 3,
    }
    deduped: list[Question] = []
    keys: list[str] = []
    for question in sorted(questions, key=lambda q: priority_order.get(q.priority, 4)):
        key = normalize_for_match(question.question)
        if any(fuzz.ratio(key, existing) >= 88 for existing in keys):
            continue
        deduped.append(question)
        keys.append(key)
    for index, question in enumerate(deduped):
        question.rank = index
    return deduped


# --------------------------------------------------------------- formatting ---
def _format_claims(summaries: list[dict[str, Any]]) -> str:
    if not summaries:
        return "(no claims were extracted)"
    blocks: list[str] = []
    for item in summaries:
        lines = [
            f"[{item['ref']}] {item['statement']}",
            f"    page {item.get('page_number')} | category={item.get('category')} "
            f"| deck evidence tier={item.get('claimed_tier')} "
            f"| thesis-critical={item.get('is_thesis_critical')} "
            f"| credibility={item.get('credibility_score')}/100 ({item.get('band')})",
            f"    evidence: {item.get('supporting_count', 0)} supporting, "
            f"{item.get('contradicting_count', 0)} contradicting, "
            f"{item.get('neutral_count', 0)} neutral",
        ]
        if item.get("verdict"):
            lines.append(f"    verdict: {truncate(item['verdict'], 400)}")
        if item.get("top_evidence"):
            for evidence in item["top_evidence"][:3]:
                lines.append(
                    f"      - [{evidence['stance']}] {evidence['citation']}: "
                    f"{truncate(evidence.get('quote') or evidence.get('rationale', ''), 200)}"
                )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _format_signals(findings: list[RuleFinding]) -> str:
    if not findings:
        return "(no deterministic signals fired)"
    return "\n".join(
        f"- [{f.severity.value}] {f.title} ({f.rule_id})"
        + (f" — claim {f.claim_id}" if f.claim_id else "")
        + f"\n    {truncate(f.description, 300)}"
        for f in findings
    )


def _resolve_refs(refs: list[str], mapping: dict[str, str]) -> list[str]:
    resolved: list[str] = []
    for ref in refs:
        claim_id = mapping.get(ref.strip())
        if claim_id and claim_id not in resolved:
            resolved.append(claim_id)
    return resolved
