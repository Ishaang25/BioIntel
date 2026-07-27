import Link from 'next/link';
import { notFound } from 'next/navigation';

import { ClaimExplorer } from '@/components/ClaimExplorer';
import { ReportView } from '@/components/ReportView';
import { RunProgress } from '@/components/RunProgress';
import { RunTabs, type Tab } from '@/components/RunTabs';
import {
  Callout,
  Chip,
  EmptyState,
  PageHeader,
  PriorityChip,
  ScoreDial,
  SeverityChip,
  Stat,
  formatDuration,
  humanise,
} from '@/components/ui';
import { api, ApiRequestError, optional } from '@/lib/api';
import type {
  Claim,
  Entity,
  EvidenceLink,
  ProgressEvent,
  Question,
  Risk,
  RunDetail,
} from '@/lib/types';

export const dynamic = 'force-dynamic';

export default async function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;

  let run: RunDetail;
  try {
    run = await api.getRun(runId);
  } catch (error) {
    if (error instanceof ApiRequestError && error.status === 404) notFound();
    throw error;
  }

  const finished = run.status === 'succeeded';

  const [report, claims, questions, risks, entities] = finished
    ? await Promise.all([
        optional(api.getReport(runId)),
        api.listClaims(runId).then((page) => page.items),
        api.listQuestions(runId),
        api.listRisks(runId),
        api.listEntities(runId),
      ])
    : [null, [] as Claim[], [] as Question[], [] as Risk[], [] as Entity[]];

  // Evidence lives on the claim detail endpoint; fetch it for the claims that
  // actually have any, in parallel.
  const evidenceByClaim: Record<string, EvidenceLink[]> = {};
  if (finished && claims.length > 0) {
    const withEvidence = claims.filter(
      (claim) =>
        (claim.assessment?.supporting_count ?? 0) +
          (claim.assessment?.contradicting_count ?? 0) +
          (claim.assessment?.neutral_count ?? 0) >
        0,
    );
    const details = await Promise.all(
      withEvidence.map((claim) => optional(api.getClaim(runId, claim.id))),
    );
    for (const detail of details) {
      if (detail) evidenceByClaim[detail.id] = detail.evidence_links;
    }
  }

  const company = run.profile?.company_name ?? run.document.filename;
  const progress: ProgressEvent = {
    run_id: run.id,
    status: run.status,
    current_stage: run.current_stage,
    progress: run.progress,
    error_code: run.error_code,
    error_message: run.error_message,
    stages: run.stages.map((stage) => ({
      stage: stage.stage,
      status: stage.status,
      duration_ms: stage.duration_ms,
      error: stage.error_message,
    })),
    counts: run.counts,
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={company}
        back={{ href: `/documents/${run.document_id}`, label: run.document.filename }}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <Chip>{run.status}</Chip>
            {run.profile?.lead_indication && <Chip>{run.profile.lead_indication}</Chip>}
            {run.profile?.modality && <Chip>{run.profile.modality}</Chip>}
            {run.profile?.development_stage && <Chip>{humanise(run.profile.development_stage)}</Chip>}
            <span className="text-ink-400">{formatDuration(run.duration_ms)}</span>
          </span>
        }
        actions={
          finished && report ? (
            <>
              <a className="btn-secondary" href={api.reportExportUrl(runId, 'markdown')} download>
                Export Markdown
              </a>
              <a
                className="btn-primary"
                href={api.reportExportUrl(runId, 'html')}
                target="_blank"
                rel="noreferrer"
              >
                Open memo
              </a>
            </>
          ) : null
        }
      />

      {run.degraded && (
        <Callout tone="warning" title="Degraded analysis">
          This run was produced without a language-model provider. Claims and evidence were matched
          by deterministic lexical rules; figures were not interpreted and evidence was not
          semantically adjudicated. Configure <code className="font-mono">OPENAI_API_KEY</code> and
          re-run before relying on this.
        </Callout>
      )}

      {run.status !== 'succeeded' && <RunProgress runId={runId} initial={progress} />}

      {run.status === 'failed' && (
        <Callout tone="danger" title="This analysis did not complete">
          {run.error_message ?? 'The pipeline failed. Check the backend logs for detail.'}{' '}
          <Link href={`/documents/${run.document_id}`} className="underline">
            Return to the document
          </Link>{' '}
          to start a new run.
        </Callout>
      )}

      {finished && report && (
        <>
          <div className="card grid gap-6 sm:grid-cols-[auto_1fr]">
            <ScoreDial score={report.overall_score} band={report.overall_band} />
            <div className="grid grid-cols-2 gap-5 sm:grid-cols-4">
              <Stat label="Claims" value={run.counts.claims ?? 0} hint="extracted and verified" />
              <Stat
                label="Evidence"
                value={run.counts.evidence_links ?? 0}
                hint="adjudicated records"
              />
              <Stat label="Risks" value={run.counts.risks ?? 0} hint="scientific findings" />
              <Stat
                label="Confidence"
                value={report.confidence.toFixed(2)}
                hint="in this assessment"
              />
            </div>
          </div>

          <RunTabs
            tabs={
              [
                { id: 'memo', label: 'IC memo', content: <ReportView report={report} /> },
                {
                  id: 'claims',
                  label: 'Claims',
                  count: claims.length,
                  content: <ClaimExplorer claims={claims} evidenceByClaim={evidenceByClaim} />,
                },
                {
                  id: 'questions',
                  label: 'Diligence questions',
                  count: questions.length,
                  content: <QuestionList questions={questions} />,
                },
                {
                  id: 'risks',
                  label: 'Risks',
                  count: risks.length,
                  content: <RiskList risks={risks} />,
                },
                {
                  id: 'entities',
                  label: 'Entities',
                  count: entities.length,
                  content: <EntityList entities={entities} />,
                },
              ] satisfies Tab[]
            }
          />
        </>
      )}

      {finished && !report && (
        <EmptyState
          title="No memo was produced"
          description="The analysis completed but the report stage did not write a memo. Check the run metrics for the stage error."
        />
      )}
    </div>
  );
}

function QuestionList({ questions }: { questions: Question[] }) {
  if (questions.length === 0) {
    return <EmptyState title="No questions" description="No diligence questions were generated." />;
  }
  return (
    <ol className="space-y-3">
      {questions.map((question, index) => (
        <li key={question.id} className="card">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 font-mono text-sm tabular-nums text-ink-400">{index + 1}</span>
            <div className="min-w-0 flex-1">
              <p className="font-medium leading-snug">{question.question}</p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <PriorityChip priority={question.priority} />
                <Chip>{humanise(question.category)}</Chip>
              </div>
              <p className="mt-2 text-sm text-ink-600 dark:text-ink-400">
                <span className="label">Why</span> {question.rationale}
              </p>
              {question.what_good_looks_like && (
                <p className="mt-1.5 text-sm text-ink-600 dark:text-ink-400">
                  <span className="label">A good answer</span> {question.what_good_looks_like}
                </p>
              )}
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}

function RiskList({ risks }: { risks: Risk[] }) {
  if (risks.length === 0) {
    return <EmptyState title="No risks recorded" description="No scientific risks were identified." />;
  }
  return (
    <ul className="space-y-3">
      {risks.map((risk) => (
        <li key={risk.id} className="card">
          <div className="flex flex-wrap items-center gap-2">
            <SeverityChip severity={risk.severity} />
            <Chip>{humanise(risk.category)}</Chip>
            {risk.is_rule_based && <Chip tone="accent">deterministic rule</Chip>}
          </div>
          <h3 className="mt-2 font-medium">{risk.title}</h3>
          <p className="mt-1 text-sm text-ink-600 dark:text-ink-400">{risk.description}</p>
          {risk.source_pages.length > 0 && (
            <p className="mt-2 text-xs text-ink-500">Pages {risk.source_pages.join(', ')}</p>
          )}
        </li>
      ))}
    </ul>
  );
}

function EntityList({ entities }: { entities: Entity[] }) {
  if (entities.length === 0) {
    return <EmptyState title="No entities" description="No scientific entities were extracted." />;
  }
  const grouped = entities.reduce<Record<string, Entity[]>>((accumulator, entity) => {
    (accumulator[entity.entity_type] ??= []).push(entity);
    return accumulator;
  }, {});

  return (
    <div className="grid gap-4 md:grid-cols-2">
      {Object.entries(grouped).map(([type, group]) => (
        <div key={type} className="card">
          <h3 className="text-sm font-semibold">{humanise(type)}</h3>
          <ul className="mt-2 space-y-2">
            {group.map((entity) => (
              <li key={entity.id} className="text-sm">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="font-medium">{entity.canonical_name ?? entity.name}</span>
                  <span className="font-mono text-xs tabular-nums text-ink-400">
                    {entity.salience.toFixed(2)}
                  </span>
                </div>
                {entity.description && (
                  <p className="text-xs text-ink-500">{entity.description}</p>
                )}
                {entity.source_pages.length > 0 && (
                  <p className="text-xs text-ink-400">pages {entity.source_pages.join(', ')}</p>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
