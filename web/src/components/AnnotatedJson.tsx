import clsx from 'clsx'
import { useState } from 'react'
import type { FieldMeta, Issue, RunResult } from '@/api/models'

interface Props {
  result: RunResult
  selected: string | null
  onSelect: (pointer: string | null) => void
  /** Pointers with pending reviewer edits (review workspace). */
  edited?: Record<string, unknown>
}

/**
 * The result as a tree. Every leaf knows its JSON pointer; clicking one selects it so the
 * evidence viewer can highlight where it came from. Status, issues and evidence counts are
 * shown inline so a reader sees runtime structure, not just values.
 */
export function AnnotatedJson({ result, selected, onSelect, edited = {} }: Props) {
  const issues = new Map(result.validation.issues.map((i) => [i.id, i]))
  return (
    <div className="mono leading-6" data-testid="annotated-json">
      <Node
        value={result.result}
        pointer=""
        name={null}
        meta={result.field_meta}
        issues={issues}
        selected={selected}
        onSelect={onSelect}
        edited={edited}
        depth={0}
      />
    </div>
  )
}

interface NodeProps {
  value: unknown
  pointer: string
  name: string | null
  meta: Record<string, FieldMeta>
  issues: Map<string, Issue>
  selected: string | null
  onSelect: (pointer: string | null) => void
  edited: Record<string, unknown>
  depth: number
}

function Node({ value, pointer, name, meta, issues, selected, onSelect, edited, depth }: NodeProps) {
  const [open, setOpen] = useState(depth < 3)
  const isContainer = value !== null && typeof value === 'object'
  const label = name === null ? null : <span className="text-ink-600">{name}: </span>

  if (!isContainer) {
    const m = meta[pointer]
    const fieldIssues = (m?.issues ?? []).map((id) => issues.get(id)).filter((i): i is Issue => !!i)
    const worst = fieldIssues.some((i) => i.severity === 'error') ? 'error' : fieldIssues.length ? 'warning' : null
    const isEdited = pointer in edited
    const shown = isEdited ? edited[pointer] : value
    return (
      <div style={{ paddingLeft: depth * 14 }}>
        <button
          type="button"
          data-pointer={pointer}
          aria-pressed={selected === pointer}
          onClick={() => onSelect(selected === pointer ? null : pointer)}
          className={clsx(
            'inline-flex max-w-full items-baseline gap-1.5 rounded px-1 text-left hover:bg-ink-100',
            selected === pointer && 'bg-amber-100 ring-1 ring-amber-400',
          )}
        >
          {label}
          <span className={clsx('truncate', valueClass(shown), isEdited && 'underline decoration-amber-500 decoration-2')}>
            {render(shown)}
          </span>
          {isEdited && <span className="text-[10px] text-amber-700">edited</span>}
          {m && m.status !== 'found' && <span className="rounded bg-ink-100 px-1 text-[10px] text-ink-600">{m.status}</span>}
          {m && m.evidence.length > 0 && (
            <span className="text-[10px] text-accent-600" title={`${m.evidence.length} evidence`}>
              ◎{m.evidence.length > 1 ? m.evidence.length : ''}
            </span>
          )}
          {worst && (
            <span className={clsx('rounded px-1 text-[10px]', worst === 'error' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-800')}>
              {fieldIssues.length} issue{fieldIssues.length > 1 ? 's' : ''}
            </span>
          )}
        </button>
      </div>
    )
  }

  const entries: [string, unknown][] = Array.isArray(value)
    ? value.map((v, i) => [String(i), v])
    : Object.entries(value as Record<string, unknown>)
  const bracket = Array.isArray(value) ? ['[', ']'] : ['{', '}']
  return (
    <div style={{ paddingLeft: depth === 0 ? 0 : depth * 14 }}>
      <button type="button" className="text-ink-500 hover:text-ink-900" onClick={() => setOpen(!open)} aria-expanded={open}>
        {label}
        {bracket[0]}
        {!open && <span className="text-ink-400"> … {entries.length} </span>}
        {!open && bracket[1]}
      </button>
      {open && (
        <>
          {entries.map(([k, v]) => (
            <Node
              key={k}
              value={v}
              pointer={`${pointer}/${k.replace(/~/g, '~0').replace(/\//g, '~1')}`}
              name={k}
              meta={meta}
              issues={issues}
              selected={selected}
              onSelect={onSelect}
              edited={edited}
              depth={depth + 1}
            />
          ))}
          <div style={{ paddingLeft: 0 }} className="text-ink-500">
            {bracket[1]}
          </div>
        </>
      )}
    </div>
  )
}

function valueClass(v: unknown): string {
  if (v === null || v === undefined) return 'text-ink-400 italic'
  if (typeof v === 'number') return 'text-violet-700'
  if (typeof v === 'boolean') return 'text-amber-700'
  return 'text-emerald-800'
}

function render(v: unknown): string {
  if (v === null || v === undefined) return 'null'
  if (typeof v === 'string') return JSON.stringify(v)
  return String(v)
}
