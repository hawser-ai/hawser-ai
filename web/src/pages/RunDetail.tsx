import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useApprovals, useCancelRun, useDecideApproval, useEvents, useResult, useRevisions, useRun, useRunRequest, useSteps } from '@/api/hooks'
import type { Artifact, RunResult } from '@/api/models'
import { api } from '@/api/client'
import { AnnotatedJson } from '@/components/AnnotatedJson'
import { EventTimeline } from '@/components/EventTimeline'
import { EvidenceViewer } from '@/components/EvidenceViewer'
import { IssueList } from '@/components/IssueList'
import { PageHeader } from '@/components/Layout'
import { PlanGraph } from '@/components/PlanGraph'
import { StatusBadge } from '@/components/StatusBadge'
import { UsageBadge } from '@/components/UsageBadge'
import { Card, ErrorBox, Json, KeyValue, Spinner, Tabs } from '@/components/ui'
import { fieldRows } from '@/lib/evidence'
import { ms, usd, when } from '@/lib/format'
import { RESTED } from '@/api/models'

type Tab = 'result' | 'plan' | 'trace' | 'validation' | 'artifacts' | 'revisions'

export function RunDetail() {
  const { id = '' } = useParams()
  const run = useRun(id)
  const rested = !!run.data && RESTED.has(run.data.status)
  const result = useResult(id, rested)
  const events = useEvents(id)
  const steps = useSteps(id)
  const revisions = useRevisions(id)
  const approvals = useApprovals(id)
  const request = useRunRequest(id)
  const cancel = useCancelRun()
  const decide = useDecideApproval()
  const [tab, setTab] = useState<Tab>('result')
  const [selected, setSelected] = useState<string | null>(null)

  useEffect(() => {
    if (!rested) {
      const t = setInterval(() => {
        void events.refetch()
        void steps.refetch()
      }, 1500)
      return () => clearInterval(t)
    }
    void events.refetch()
    void steps.refetch()
  }, [rested]) // eslint-disable-line react-hooks/exhaustive-deps

  if (run.isLoading) return <Spinner />
  if (run.error) return <ErrorBox error={run.error} />
  const summary = run.data!
  const r: RunResult | undefined = result.data

  return (
    <div>
      <PageHeader
        title={
          <span className="flex items-center gap-3">
            <span className="mono text-base">{id}</span>
            <StatusBadge status={summary.status} />
          </span>
        }
        subtitle={
          <span>
            mode {summary.mode ?? '—'} · {summary.skill ? `skill ${summary.skill.name}` : 'no skill'} · created {when(summary.created_at)}
            {r?.revision_id && <> · revision <span className="mono">{r.revision_id}</span></>}
          </span>
        }
        actions={
          <>
            {summary.status === 'needs_review' && (
              <Link className="btn-primary" to={`/runs/${id}/review`} data-testid="open-review">
                open review
              </Link>
            )}
            {!RESTED.has(summary.status) && (
              <button className="btn-danger" onClick={() => cancel.mutate(id)} disabled={cancel.isPending}>
                cancel
              </button>
            )}
          </>
        }
      />
      {r && (
        <div className="mb-2">
          <UsageBadge usage={r.usage} maxCost={request.data?.policy.limits.max_cost_usd ?? null} />
        </div>
      )}
      {request.data && (
        <details className="mb-4 text-xs text-ink-600">
          <summary className="cursor-pointer">request: {request.data.instruction ?? `skill ${request.data.skill?.name}`} · {request.data.inputs.length} input(s) · budget {request.data.policy.limits.max_cost_usd ?? '∞'}</summary>
          <Json value={request.data} />
        </details>
      )}
      {summary.status === 'awaiting_approval' && approvals.data && approvals.data.length > 0 && (
        <Card title="Pending approvals" className="mb-4">
          <ul className="space-y-2 text-sm">
            {approvals.data.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center gap-2">
                <span className="mono">{a.tool}</span> <span className="text-ink-500">action {a.action_id}</span>
                <Json value={a.payload} className="max-h-24 flex-1" />
                <button className="btn-primary text-xs" onClick={() => decide.mutate({ id: a.id, approve: true, by: 'web' })}>
                  approve & resume
                </button>
                <button className="btn-danger text-xs" onClick={() => decide.mutate({ id: a.id, approve: false, by: 'web' })}>
                  reject
                </button>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {r?.error && (
        <div className="mb-4">
          <ErrorBox error={{ code: r.error.code, message: r.error.message }} />
        </div>
      )}
      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        tabs={[
          { id: 'result', label: 'Result' },
          { id: 'plan', label: 'Plan' },
          { id: 'trace', label: `Trace (${events.data?.length ?? 0})` },
          { id: 'validation', label: `Validation${r ? ` (${r.validation.issues.length})` : ''}` },
          { id: 'artifacts', label: `Artifacts (${r?.artifacts.length ?? 0})` },
          { id: 'revisions', label: `Revisions (${revisions.data?.length ?? 0})` },
        ]}
      />
      <div className="mt-4">
        {tab === 'result' &&
          (r ? (
            r.result === null || r.result === undefined ? (
              <Card title="Result">
                <p className="text-sm text-ink-500">No result for this run.</p>
              </Card>
            ) : (
              <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
                <Card title="Result" actions={<span className="text-xs text-ink-500">click a field to see its evidence</span>}>
                  <AnnotatedJson result={r} selected={selected} onSelect={setSelected} />
                  <FieldsTable result={r} selected={selected} onSelect={setSelected} />
                </Card>
                <Card title="Evidence">
                  <EvidenceViewer result={r} selectedPointer={selected} onSelectPointer={setSelected} />
                </Card>
              </div>
            )
          ) : (
            <Spinner label={rested ? 'Loading result…' : 'Run in progress — result appears when it rests'} />
          ))}
        {tab === 'plan' && <PlanGraph events={events.data ?? []} steps={steps.data ?? []} />}
        {tab === 'trace' && (
          <div className="grid gap-4 lg:grid-cols-[1fr_minmax(0,22rem)]">
            <Card title="Events">
              <EventTimeline events={events.data ?? []} live={!rested} />
            </Card>
            <div className="space-y-4">
              <Card title="Steps">
                <table className="table">
                  <thead>
                    <tr>
                      <th>key</th>
                      <th>status</th>
                      <th>ms</th>
                      <th>cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(steps.data ?? []).map((s) => (
                      <tr key={s.id}>
                        <td className="mono">{s.key}</td>
                        <td>
                          <StatusBadge status={s.status} />
                        </td>
                        <td>{s.started_at && s.completed_at ? ms(new Date(s.completed_at).getTime() - new Date(s.started_at).getTime()) : '—'}</td>
                        <td>{usd(s.usage.totals.cost_usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
              {r && (
                <Card title="Model calls">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>model</th>
                        <th>tokens</th>
                        <th>cost</th>
                        <th>ms</th>
                      </tr>
                    </thead>
                    <tbody>
                      {r.usage.model_calls.map((c, i) => (
                        <tr key={i}>
                          <td className="mono">
                            {c.provider}/{c.model}
                            {c.replayed && <span className="ml-1 text-[10px] text-ink-400">replayed</span>}
                          </td>
                          <td>
                            {c.input_tokens} / {c.output_tokens}
                          </td>
                          <td>{usd(c.cost_usd)}</td>
                          <td>{c.latency_ms}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              )}
            </div>
          </div>
        )}
        {tab === 'validation' && (r ? <IssueList report={r.validation} selected={selected} onSelect={(p) => { setSelected(p); setTab('result') }} /> : <Spinner />)}
        {tab === 'artifacts' && (r ? <ArtifactsTab result={r} /> : <Spinner />)}
        {tab === 'revisions' && (
          <div className="space-y-3">
            {(revisions.data ?? []).map((rev) => (
              <Card key={rev.id} title={<span className="mono">{rev.id} · {rev.kind} · {rev.author}</span>} actions={<span className="text-xs text-ink-500">{when(rev.created_at)}</span>}>
                {rev.comment && <p className="mb-2 text-sm text-ink-700">{rev.comment}</p>}
                <Json value={rev.result} />
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function FieldsTable({ result, selected, onSelect }: { result: RunResult; selected: string | null; onSelect: (p: string) => void }) {
  const rows = fieldRows(result)
  return (
    <details className="mt-3">
      <summary className="cursor-pointer text-xs text-ink-500">fields table ({rows.length})</summary>
      <table className="table mt-2">
        <thead>
          <tr>
            <th>field</th>
            <th>status</th>
            <th>evidence</th>
            <th>conf</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((f) => (
            <tr key={f.pointer} className={selected === f.pointer ? 'bg-amber-50' : ''}>
              <td>
                <button className="mono text-accent-700 hover:underline" onClick={() => onSelect(f.pointer)}>
                  {f.pointer}
                </button>
              </td>
              <td>{f.meta.status}</td>
              <td className="text-xs text-ink-600">
                {f.evidence.map((e) => (e.kind === 'document_region' ? `p${e.page}` : e.kind)).join(', ') || '—'}
              </td>
              <td>{f.meta.anchor_confidence?.toFixed(2) ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  )
}

function ArtifactsTab({ result }: { result: RunResult }) {
  const [items, setItems] = useState<Artifact[]>([])
  useEffect(() => {
    let alive = true
    Promise.all(result.artifacts.map((a) => api<Artifact>(`/v1/artifacts/${a.artifact_id}`).catch(() => null))).then((xs) => {
      if (alive) setItems(xs.filter((x): x is Artifact => !!x))
    })
    return () => {
      alive = false
    }
  }, [result])
  const byId = new Map(items.map((a) => [a.id, a]))
  return (
    <Card title="Artifacts and lineage">
      <table className="table">
        <thead>
          <tr>
            <th>artifact</th>
            <th>kind</th>
            <th>type</th>
            <th>size</th>
            <th>parents</th>
            <th>producer</th>
          </tr>
        </thead>
        <tbody>
          {items.map((a) => (
            <tr key={a.id}>
              <td className="mono">{a.id}</td>
              <td>{a.meta.kind}</td>
              <td>{a.meta.media_type}</td>
              <td>{(a.meta.size_bytes / 1024).toFixed(1)} KB</td>
              <td className="mono text-xs">
                {a.meta.parents.map((p) => (
                  <div key={p}>
                    {p}
                    {byId.get(p) ? ` (${byId.get(p)!.meta.kind})` : ''}
                  </div>
                ))}
                {!a.meta.parents.length && '—'}
              </td>
              <td className="text-xs">
                {a.meta.producer.kind}:{a.meta.producer.name}
                {a.meta.transform ? ` · ${JSON.stringify(a.meta.transform)}` : ''}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!items.length && <p className="text-sm text-ink-500">Loading…</p>}
      <KeyValue items={[{ k: 'trace id', v: <span className="mono">{result.trace_id}</span> }]} />
    </Card>
  )
}
