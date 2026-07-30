'use client';

import { useMemo, useState } from 'react';

import { humanise } from '@/lib/format';
import { useChecklist } from '@/lib/use-checklist';
import type { Question, QuestionPriority } from '@/lib/types';
import { Badge, Card, EmptyState, Meter, PriorityBadge, cx } from '@/components/ui/primitives';
import { useReport } from '../context';
import { Section } from './Section';

const PRIORITY_ORDER: QuestionPriority[] = ['critical', 'high', 'medium', 'low'];

/**
 * The questions to put to management, as work rather than as a bullet list.
 *
 * Each item is a task: tick it when it has been asked and answered. Progress is
 * local to the browser — see `useChecklist` for why that is deliberate.
 */
export function QuestionsSection() {
  const { model, openClaim } = useReport();
  const { done, hydrated, toggle, clear } = useChecklist(`biointel:checklist:${model.runId}`);
  const [hideDone, setHideDone] = useState(false);

  const grouped = useMemo(() => {
    const buckets = new Map<QuestionPriority, Question[]>();
    for (const question of model.questions) {
      const bucket = buckets.get(question.priority);
      if (bucket) bucket.push(question);
      else buckets.set(question.priority, [question]);
    }
    return PRIORITY_ORDER.filter((priority) => buckets.has(priority)).map((priority) => ({
      priority,
      questions: buckets.get(priority) ?? [],
    }));
  }, [model.questions]);

  const completed = model.questions.filter((question) => done.has(question.id)).length;
  const progress = model.questions.length === 0 ? 0 : (completed / model.questions.length) * 100;

  if (model.questions.length === 0) {
    return (
      <Section id="questions" title="Management questions">
        <EmptyState
          compact
          title="No questions were generated"
          description="The diligence stage did not produce questions for this analysis."
        />
      </Section>
    );
  }

  return (
    <Section
      id="questions"
      title="Management questions"
      description="The specific technical questions to put to the company, ordered by what would change the investment decision. Tick items as they are answered — progress is kept in this browser only."
      actions={
        <div className="flex items-center gap-2">
          <label className="flex cursor-pointer select-none items-center gap-1.5 rounded-md px-2 py-1.5 text-[13px] text-fg-2 hover:bg-subtle hover:text-fg">
            <input
              type="checkbox"
              checked={hideDone}
              onChange={(event) => setHideDone(event.target.checked)}
              className="h-3.5 w-3.5 accent-current"
            />
            Hide answered
          </label>
          {completed > 0 && (
            <button type="button" className="btn btn-sm btn-ghost" onClick={clear}>
              Reset
            </button>
          )}
        </div>
      }
    >
      <Card className="mb-4 flex flex-wrap items-center gap-4 px-5 py-4">
        <div className="min-w-[10rem] flex-1">
          <div className="flex items-baseline justify-between gap-3">
            <span className="label">Diligence progress</span>
            <span className="num text-[13px] font-medium text-fg">
              {completed} / {model.questions.length}
            </span>
          </div>
          <Meter
            className="mt-2"
            value={progress}
            tone={progress === 100 ? 'pos' : 'neutral'}
            label="Questions answered"
          />
        </div>
        <div className="flex flex-wrap gap-1.5">
          {grouped.map((group) => (
            <Badge key={group.priority} tone={group.priority === 'critical' ? 'crit' : 'neutral'}>
              {group.questions.length} {humanise(group.priority).toLowerCase()}
            </Badge>
          ))}
        </div>
      </Card>

      <div className="space-y-5">
        {grouped.map((group) => {
          const visible = hideDone
            ? group.questions.filter((question) => !done.has(question.id))
            : group.questions;
          if (visible.length === 0) return null;

          return (
            <div key={group.priority}>
              <h3 className="label mb-2">
                {humanise(group.priority)} priority · {visible.length}
              </h3>
              <ul className="space-y-2">
                {visible.map((question) => (
                  <QuestionItem
                    key={question.id}
                    question={question}
                    checked={done.has(question.id)}
                    disabled={!hydrated}
                    onToggle={() => toggle(question.id)}
                    onTraceClaim={openClaim}
                    claimIds={question.related_claim_ids.filter((id) => model.claimsById.has(id))}
                  />
                ))}
              </ul>
            </div>
          );
        })}
      </div>
    </Section>
  );
}

function QuestionItem({
  question,
  checked,
  disabled,
  onToggle,
  onTraceClaim,
  claimIds,
}: {
  question: Question;
  checked: boolean;
  disabled: boolean;
  onToggle: () => void;
  onTraceClaim: (claimId: string) => void;
  claimIds: string[];
}) {
  return (
    <Card as="li" className={cx('p-0 transition-opacity', checked && 'opacity-60')}>
      <div className="flex gap-3 p-4">
        <label className="mt-0.5 flex shrink-0 cursor-pointer self-start">
          <input
            type="checkbox"
            checked={checked}
            disabled={disabled}
            onChange={onToggle}
            aria-label={`Mark answered: ${question.question}`}
            className="h-4 w-4 cursor-pointer rounded border-line accent-current"
          />
        </label>

        <div className="min-w-0 flex-1">
          <p
            className={cx(
              'text-[14px] font-medium leading-snug text-fg',
              checked && 'line-through decoration-fg-3',
            )}
          >
            {question.question}
          </p>

          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <PriorityBadge priority={question.priority} />
            <Badge>{humanise(question.category)}</Badge>
            {claimIds.length > 0 && (
              <button
                type="button"
                onClick={() => onTraceClaim(claimIds[0]!)}
                className="chip border-line bg-subtle text-fg-2 transition-colors hover:border-line-strong hover:text-fg"
              >
                Trace {claimIds.length === 1 ? 'claim' : `${claimIds.length} claims`} →
              </button>
            )}
          </div>

          <dl className="mt-3 grid gap-2.5 border-t border-line pt-3 text-[13px] leading-relaxed sm:grid-cols-2">
            <div>
              <dt className="label">Why it matters</dt>
              <dd className="mt-1 text-fg-2">{question.rationale}</dd>
            </div>
            {question.what_good_looks_like && (
              <div>
                <dt className="label">What a good answer looks like</dt>
                <dd className="mt-1 text-fg-2">{question.what_good_looks_like}</dd>
              </div>
            )}
          </dl>
        </div>
      </div>
    </Card>
  );
}
