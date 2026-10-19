import clsx from 'clsx'
import type { Issue, RunResult } from '@/api/models'
import { leafPointers, parentPointer } from '@/lib/json'
import { getPointer } from '@/lib/json'

interface Props {
  result: RunResult
  /** The current value with already-recorded review edits applied. */
  current: unknown
  /** Pending (unsaved) edits by pointer. */
  pending: Record<string, unknown>
  onEdit: (pointer: string, value: unknown) => void
  onRevert: (pointer: string) => void
  selected: string | null
  onSelect: (pointer: string | null) => void
  /** Pointers to offer even when the result has no value there (from the output schema). */
  extraPointers?: string[]
}

/**
 * The result as an editable form, generated from the result's shape (every leaf becomes a
 * field), with the machine's issues inline and change tracking against the reviewed value.
 */
export function ResultForm({ result, current, pending, onEdit, onRevert, selected, onSelect, extraPointers = [] }: Props) {
  const issues = new Map<string, Issue[]>()
  for (const i of result.validation.issues) if (i.field) issues.set(i.field, [...(issues.get(i.field) ?? []), i])
  const pointers = [...new Set([...Object.keys(result.field_meta), ...leafPointers(current), ...extraPointers])]
    .filter((p) => p !== '')
    .sort((a, b) =>
    a.localeCompare(b, undefined, { numeric: true }),
  )
  const groups = new Map<string, string[]>()
  for (const p of pointers) groups.set(parentPointer(p), [...(groups.get(parentPointer(p)) ?? []), p])

  return (
    <div className="space-y-4" data-testid="result-form">
      {[...groups.entries()].map(([parent, ptrs]) => (
        <fieldset key={parent || '(root)'} className="rounded border border-ink-200">
          <legend className="mono px-1 text-xs text-ink-500">{parent || 'root'}</legend>
          <div className="divide-y divide-ink-100">
            {ptrs.map((p) => {
              const machine = getPointer(result.result, p)
              const reviewed = getPointer(current, p)
              const value = p in pending ? pending[p] : reviewed
              const meta = result.field_meta[p]
              const fieldIssues = issues.get(p) ?? []
              const changedBefore = JSON.stringify(reviewed) !== JSON.stringify(machine)
              const changedNow = p in pending && JSON.stringify(pending[p]) !== JSON.stringify(reviewed)
              return (
                <div
                  key={p}
                  className={clsx('grid grid-cols-[minmax(8rem,14rem)_1fr] items-start gap-2 px-3 py-1.5', selected === p && 'bg-amber-50')}
                  onFocus={() => onSelect(p)}
                >
                  <div className="min-w-0">
                    <button type="button" className="mono block max-w-full truncate text-left text-xs text-accent-700 hover:underline" onClick={() => onSelect(p)}>
                      {p.slice(parent.length + 1) || p}
                    </button>
                    <div className="flex flex-wrap gap-1 text-[10px]">
                      {meta && meta.status !== 'found' && <span className="rounded bg-ink-100 px-1 text-ink-600">{meta.status}</span>}
                      {meta && meta.evidence.length > 0 && <span className="text-accent-600">◎ evidence</span>}
                      {fieldIssues.map((i) => (
                        <span key={i.id} className={clsx('rounded px-1', i.severity === 'error' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-800')} title={i.message}>
                          {i.rule_id}
                        </span>
                      ))}
                      {changedBefore && !changedNow && <span className="rounded bg-emerald-50 px-1 text-emerald-700">edited (saved)</span>}
                      {changedNow && <span className="rounded bg-amber-100 px-1 text-amber-800">changed</span>}
                    </div>
                  </div>
                  <div className="flex items-start gap-1">
                    <ValueInput value={value} onChange={(v) => onEdit(p, v)} pointer={p} />
                    {p in pending && (
                      <button type="button" className="btn-ghost text-xs" onClick={() => onRevert(p)} aria-label={`revert ${p}`}>
                        ↺
                      </button>
                    )}
                  </div>
                  {fieldIssues.length > 0 && <div className="col-span-2 text-xs text-ink-600">{fieldIssues.map((i) => i.message).join(' · ')}</div>}
                </div>
              )
            })}
          </div>
        </fieldset>
      ))}
    </div>
  )
}

function ValueInput({ value, onChange, pointer }: { value: unknown; onChange: (v: unknown) => void; pointer: string }) {
  if (typeof value === 'boolean')
    return (
      <select className="input" value={String(value)} onChange={(e) => onChange(e.target.value === 'true')} aria-label={pointer}>
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    )
  if (typeof value === 'number')
    return <input className="input" type="number" step="any" value={value} aria-label={pointer} onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))} />
  if (value === null || value === undefined)
    return <input className="input italic" placeholder="null (unknown) — type a value" aria-label={pointer} value="" onChange={(e) => onChange(coerce(e.target.value))} />
  if (typeof value === 'object')
    return <textarea className="input mono" rows={2} aria-label={pointer} value={JSON.stringify(value)} onChange={(e) => onChange(coerce(e.target.value))} />
  return <input className="input" value={String(value)} aria-label={pointer} onChange={(e) => onChange(e.target.value)} />
}

function coerce(text: string): unknown {
  if (text === '') return null
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}
