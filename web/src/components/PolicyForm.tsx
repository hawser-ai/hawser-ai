import { useState } from 'react'
import type { Policy } from '@/api/models'
import { setPointer } from '@/lib/json'
import { SchemaEditor } from './SchemaEditor'
import { Field } from './ui'

interface Props {
  value: Policy
  onChange: (policy: Policy) => void
  models?: string[]
  compact?: boolean
}

const num = (v: string): number | null => (v.trim() === '' ? null : Number(v))

/** The common policy knobs as a form; everything else through the JSON editor below it. */
export function PolicyForm({ value, onChange, models = [], compact }: Props) {
  const [advanced, setAdvanced] = useState(false)
  const [text, setText] = useState(() => JSON.stringify(value, null, 2))
  const set = (pointer: string, v: unknown) => {
    const next = setPointer(value, pointer, v)
    onChange(next)
    setText(JSON.stringify(next, null, 2))
  }
  return (
    <div className="space-y-3" data-testid="policy-form">
      <div className={compact ? 'grid grid-cols-2 gap-3' : 'grid grid-cols-2 gap-3 md:grid-cols-4'}>
        <Field label="Max cost (USD)">
          <input className="input" type="number" step="0.01" min="0" value={value.limits.max_cost_usd ?? ''} onChange={(e) => set('/limits/max_cost_usd', num(e.target.value))} />
        </Field>
        <Field label="Max model calls">
          <input className="input" type="number" min="0" value={value.limits.max_model_calls ?? ''} onChange={(e) => set('/limits/max_model_calls', num(e.target.value))} />
        </Field>
        <Field label="Max pages">
          <input className="input" type="number" min="0" value={value.limits.max_pages ?? ''} onChange={(e) => set('/limits/max_pages', num(e.target.value))} />
        </Field>
        <Field label="Repair attempts">
          <input className="input" type="number" min="0" value={value.repair.attempts} onChange={(e) => set('/repair/attempts', Number(e.target.value || 0))} />
        </Field>
        <Field label="Wall time (s)">
          <input className="input" type="number" min="0" value={value.limits.max_wall_time_s ?? ''} onChange={(e) => set('/limits/max_wall_time_s', num(e.target.value))} />
        </Field>
        <Field label="Mode">
          <select className="input" value={value.mode ?? ''} onChange={(e) => set('/mode', e.target.value || null)}>
            <option value="">skill default / direct</option>
            <option value="direct">direct</option>
            <option value="workflow">workflow</option>
            <option value="agent">agent</option>
          </select>
        </Field>
        <Field label="Allowed models" hint="empty = any policy-permitted model">
          <input
            className="input"
            list="model-ids"
            placeholder="comma-separated ids"
            value={(value.allowed_models ?? []).join(', ')}
            onChange={(e) => set('/allowed_models', e.target.value.trim() ? e.target.value.split(',').map((s) => s.trim()).filter(Boolean) : null)}
          />
          <datalist id="model-ids">
            {models.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </Field>
        <label className="flex items-end gap-2 pb-2 text-sm">
          <input type="checkbox" checked={value.review_on_failure} onChange={(e) => set('/review_on_failure', e.target.checked)} />
          review on failure
        </label>
      </div>
      <button type="button" className="btn-ghost text-xs" onClick={() => setAdvanced(!advanced)} aria-expanded={advanced}>
        {advanced ? 'hide' : 'show'} full policy JSON
      </button>
      {advanced && (
        <SchemaEditor
          label="Policy (JSON)"
          value={text}
          height="220px"
          onChange={(t) => {
            setText(t)
            try {
              onChange(JSON.parse(t) as Policy)
            } catch {
              /* shown by the editor */
            }
          }}
        />
      )}
    </div>
  )
}
