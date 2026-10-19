import clsx from 'clsx'
import { useState } from 'react'
import type { Event } from '@/api/models'
import { clock, truncate } from '@/lib/format'

const TONE: Record<string, string> = {
  'run.status_changed': 'text-accent-700',
  'step.started': 'text-ink-700',
  'step.completed': 'text-emerald-700',
  'step.failed': 'text-red-700',
  'step.skipped': 'text-ink-500',
  'model.called': 'text-violet-700',
  'model.fallback': 'text-amber-700',
  'tool.called': 'text-violet-700',
  'tool.denied': 'text-red-700',
  'validation.completed': 'text-ink-800',
  'repair.started': 'text-amber-700',
  'repair.no_progress': 'text-amber-700',
  'review.requested': 'text-amber-700',
  'approval.requested': 'text-violet-700',
  'budget.exceeded': 'text-red-700',
  'result.assembled': 'text-emerald-800',
}

function summary(e: Event): string {
  const p = e.payload as Record<string, unknown>
  switch (e.type) {
    case 'run.status_changed':
      return `${String(p.from)} → ${String(p.to)}${p.resume ? ' (resume)' : ''}`
    case 'step.started':
    case 'step.completed':
    case 'step.failed':
    case 'step.skipped':
      return String(p.key ?? '')
    case 'model.called':
      return `${String(p.model ?? '')} · ${String(p.input_tokens ?? '?')} in / ${String(p.output_tokens ?? '?')} out · $${Number(p.cost_usd ?? 0).toFixed(4)}`
    case 'model.fallback':
      return `${String(p.from)} → ${String(p.to)}`
    case 'tool.called':
      return String(p.tool ?? '')
    case 'context.selected':
      return `${String(p.estimated_tokens ?? '?')} / ${String(p.budget_tokens ?? '?')} tokens`
    case 'validation.completed':
      return `${String(p.issues ?? p.issue_count ?? '')} issue(s)`
    case 'plan.created':
      return `${String(p.source ?? '')} ${Array.isArray(p.candidates) ? (p.candidates as string[]).slice(0, 2).join(', ') : ''}`
    default:
      return truncate(JSON.stringify(p), 80)
  }
}

export function EventTimeline({ events, live }: { events: Event[]; live?: boolean }) {
  const [open, setOpen] = useState<number | null>(null)
  if (!events.length) return <div className="text-sm text-ink-500">No events yet.</div>
  return (
    <ol className="divide-y divide-ink-100 text-sm" data-testid="event-timeline" aria-live={live ? 'polite' : undefined}>
      {events.map((e) => (
        <li key={e.seq}>
          <button
            type="button"
            onClick={() => setOpen(open === e.seq ? null : e.seq)}
            className="grid w-full grid-cols-[3rem_6.5rem_12rem_1fr] items-baseline gap-2 px-1 py-1 text-left hover:bg-ink-50"
            aria-expanded={open === e.seq}
          >
            <span className="mono text-ink-400">{e.seq}</span>
            <span className="mono text-ink-500">{clock(e.at)}</span>
            <span className={clsx('mono', TONE[e.type] ?? 'text-ink-700')}>{e.type}</span>
            <span className="truncate text-ink-700">{summary(e)}</span>
          </button>
          {open === e.seq && <pre className="mono max-h-72 overflow-auto bg-ink-50 p-2 text-ink-800">{JSON.stringify(e.payload, null, 2)}</pre>}
        </li>
      ))}
    </ol>
  )
}
