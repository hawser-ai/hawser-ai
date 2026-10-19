import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { streamRunEvents } from '@/api/client'
import { useCreateRun, useModels, usePolicy, useQuickstartExample, useSkills } from '@/api/hooks'
import type { Asset, Event, Policy, RunRequest } from '@/api/models'
import { EventTimeline } from '@/components/EventTimeline'
import { FileDrop } from '@/components/FileDrop'
import { PageHeader } from '@/components/Layout'
import { PolicyForm } from '@/components/PolicyForm'
import { SchemaEditor } from '@/components/SchemaEditor'
import { StatusBadge } from '@/components/StatusBadge'
import { Card, ErrorBox, Field } from '@/components/ui'
import { tryParseJson } from '@/lib/json'

const DEFAULT_SCHEMA = JSON.stringify(
  {
    type: 'object',
    properties: {
      invoice_number: { type: 'string' },
      invoice_date: { type: 'string' },
      vendor_name: { type: 'string' },
      total: { type: 'number' },
    },
    required: ['invoice_number', 'total'],
    additionalProperties: false,
  },
  null,
  2,
)

export function Playground() {
  const navigate = useNavigate()
  const skills = useSkills()
  const models = useModels()
  const defaultPolicy = usePolicy()
  const create = useCreateRun()
  const example = useQuickstartExample()
  const [assets, setAssets] = useState<Asset[]>([])
  const [instruction, setInstruction] = useState('Extract the invoice.')
  const [schema, setSchema] = useState(DEFAULT_SCHEMA)
  const [useSchema, setUseSchema] = useState(true)
  const [skill, setSkill] = useState('')
  const [mode, setMode] = useState<'' | 'direct' | 'workflow' | 'agent'>('')
  const [policy, setPolicy] = useState<Policy | null>(null)
  const [events, setEvents] = useState<Event[]>([])
  const [runId, setRunId] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const abort = useRef<AbortController | null>(null)

  useEffect(() => {
    if (defaultPolicy.data && !policy) setPolicy(defaultPolicy.data)
  }, [defaultPolicy.data, policy])
  useEffect(() => () => abort.current?.abort(), [])

  const parsedSchema = tryParseJson(schema)
  const canRun = assets.length > 0 && (skill || instruction.trim()) && (!useSchema || (!parsedSchema.error && !!parsedSchema.value)) && !create.isPending

  const run = async () => {
    if (!policy) return
    const req: RunRequest = {
      instruction: instruction.trim() || null,
      inputs: assets.map((a) => ({ kind: 'asset', asset_id: a.id, name: a.name ?? null })),
      output_schema: useSchema && !skill ? (parsedSchema.value as Record<string, unknown>) : null,
      skill: skill ? { name: skill, version: null } : null,
      mode: mode || null,
      policy: { ...policy, mode: mode || policy.mode },
      tools: null,
      models: null,
      metadata: { source: 'playground' },
      idempotency_key: null,
    }
    setEvents([])
    setStatus('submitting')
    const summary = await create.mutateAsync(req)
    setRunId(summary.id)
    setStatus(summary.status)
    abort.current?.abort()
    abort.current = new AbortController()
    try {
      const end = await streamRunEvents(
        summary.id,
        (e) => {
          setEvents((prev) => [...prev, e])
          if (e.type === 'run.status_changed') setStatus(String(e.payload.to))
        },
        { signal: abort.current.signal },
      )
      if (end.status) setStatus(end.status)
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setStatus('stream lost')
    }
  }

  return (
    <div>
      <PageHeader
        title="Playground"
        subtitle="Upload documents, describe what you want (or pick a skill), set the policy, run, and watch the steps."
        actions={
          <button
            className="btn-secondary text-xs"
            disabled={example.isPending}
            data-testid="load-example"
            onClick={() =>
              example.mutate(undefined, {
                onSuccess: (ex) => {
                  setAssets([ex.asset])
                  setInstruction(ex.instruction)
                  setSchema(JSON.stringify(ex.output_schema, null, 2))
                  setUseSchema(true)
                  setSkill('')
                  setPolicy(ex.policy)
                },
              })
            }
          >
            load the bundled example
          </button>
        }
      />
      {example.error ? <div className="mb-3"><ErrorBox error={example.error} /></div> : null}
      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <div className="space-y-4">
          <Card title="1. Inputs">
            <FileDrop assets={assets} onChange={setAssets} />
          </Card>
          <Card title="2. Task">
            <div className="grid gap-3 md:grid-cols-2">
              <Field label="Skill" hint="A skill brings its own schema, tools and validators.">
                <select className="input" value={skill} onChange={(e) => setSkill(e.target.value)} data-testid="skill-select">
                  <option value="">none (free-form instruction)</option>
                  {(skills.data ?? []).map((s) => (
                    <option key={s.name} value={s.name}>
                      {s.name}@{s.version} — {s.description}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Mode">
                <select className="input" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
                  <option value="">default</option>
                  <option value="direct">direct — one extraction</option>
                  <option value="workflow">workflow — the skill's steps</option>
                  <option value="agent">agent — planner-built steps</option>
                </select>
              </Field>
            </div>
            <Field label="Instruction">
              <textarea className="input mt-1" rows={2} value={instruction} onChange={(e) => setInstruction(e.target.value)} data-testid="instruction" />
            </Field>
            {!skill && (
              <div className="mt-3">
                <label className="mb-2 flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={useSchema} onChange={(e) => setUseSchema(e.target.checked)} /> structured output (schema)
                </label>
                {useSchema && <SchemaEditor value={schema} onChange={setSchema} withExampleHelper />}
              </div>
            )}
          </Card>
          <Card title="3. Policy">
            {policy ? <PolicyForm value={policy} onChange={setPolicy} models={(models.data ?? []).map((m) => m.id)} compact /> : <span className="text-sm text-ink-500">loading…</span>}
          </Card>
          {create.error ? <ErrorBox error={create.error} /> : null}
          <button className="btn-primary" disabled={!canRun} onClick={() => void run()} data-testid="run-button">
            {create.isPending ? 'Submitting…' : 'Run'}
          </button>
        </div>
        <Card
          title="Live progress"
          actions={
            runId && (
              <>
                {status && <StatusBadge status={status} />}
                <button className="btn-secondary text-xs" onClick={() => navigate(`/runs/${runId}`)} data-testid="open-run">
                  open run
                </button>
              </>
            )
          }
        >
          {!runId ? (
            <p className="text-sm text-ink-500">Steps, model calls, validation and budget events appear here as the server emits them (SSE).</p>
          ) : (
            <EventTimeline events={events} live />
          )}
        </Card>
      </div>
    </div>
  )
}
