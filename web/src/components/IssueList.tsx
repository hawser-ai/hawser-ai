import clsx from 'clsx'
import type { ValidationReport } from '@/api/models'

interface Props {
  report: ValidationReport
  onSelect?: (pointer: string) => void
  selected?: string | null
}

/** Issues grouped by severity; clicking one selects the field it points at. */
export function IssueList({ report, onSelect, selected }: Props) {
  const groups: { severity: 'error' | 'warning' | 'info'; items: ValidationReport['issues'] }[] = (['error', 'warning', 'info'] as const)
    .map((severity) => ({ severity, items: report.issues.filter((i) => i.severity === severity) }))
    .filter((g) => g.items.length)
  if (!groups.length)
    return (
      <div className="text-sm text-emerald-700" data-testid="issue-list">
        No validation issues. {report.rules_run.length} rule{report.rules_run.length === 1 ? '' : 's'} ran.
      </div>
    )
  return (
    <div className="space-y-3" data-testid="issue-list">
      {groups.map((g) => (
        <section key={g.severity}>
          <h3 className={clsx('label', g.severity === 'error' ? 'text-red-700' : g.severity === 'warning' ? 'text-amber-700' : 'text-ink-500')}>
            {g.severity} · {g.items.length}
          </h3>
          <ul className="divide-y divide-ink-100 rounded border border-ink-200">
            {g.items.map((i) => (
              <li key={i.id} className={clsx('px-3 py-2 text-sm', selected && i.field === selected && 'bg-amber-50')}>
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="mono text-ink-500">{i.rule_id}</span>
                  {i.field && (
                    <button type="button" className="mono text-accent-700 hover:underline" onClick={() => onSelect?.(i.field!)}>
                      {i.field}
                    </button>
                  )}
                  {i.repair && i.repair !== 'none' && <span className="rounded bg-ink-100 px-1 text-[10px] text-ink-600">repair: {i.repair}</span>}
                  {typeof i.details?.resolved_by === 'string' && <span className="rounded bg-emerald-50 px-1 text-[10px] text-emerald-700">resolved by review</span>}
                </div>
                <div className="text-ink-800">{i.message}</div>
              </li>
            ))}
          </ul>
        </section>
      ))}
      <div className="text-xs text-ink-500">
        rules: {report.rules_run.map((r) => `${r.rule_id} (${r.issues})`).join(', ')}
      </div>
    </div>
  )
}
