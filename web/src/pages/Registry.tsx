import { useState } from 'react'
import { useModels, useSkills, useTools } from '@/api/hooks'
import type { SkillManifest } from '@/api/models'
import { PageHeader } from '@/components/Layout'
import { Badge, Card, Json, Spinner, Tabs } from '@/components/ui'

type Tab = 'models' | 'tools' | 'skills'

export function Registry() {
  const [tab, setTab] = useState<Tab>('models')
  const models = useModels()
  const tools = useTools()
  const skills = useSkills()
  return (
    <div>
      <PageHeader title="Registry" subtitle="What this deployment can run: models with capabilities and prices, tools with permissions and side effects, installed skills." />
      <Tabs<Tab> value={tab} onChange={setTab} tabs={[{ id: 'models', label: 'Models' }, { id: 'tools', label: 'Tools' }, { id: 'skills', label: 'Skills' }]} />
      <div className="mt-4">
        {tab === 'models' &&
          (models.data ? (
            <table className="table card">
              <thead>
                <tr>
                  <th>id</th>
                  <th>modalities</th>
                  <th>context</th>
                  <th>structured</th>
                  <th>$/Mtok in / out</th>
                  <th>hosted</th>
                  <th>regions</th>
                  <th>priors</th>
                </tr>
              </thead>
              <tbody>
                {models.data.map((m) => (
                  <tr key={m.id}>
                    <td className="mono">{m.id}</td>
                    <td>{m.modalities.join(', ')}</td>
                    <td>{m.context_window.toLocaleString()}</td>
                    <td>{m.structured_output ? 'yes' : 'no'}</td>
                    <td>
                      {m.pricing?.input_per_mtok ?? '—'} / {m.pricing?.output_per_mtok ?? '—'}
                    </td>
                    <td>{m.data_handling.hosted ? 'hosted' : 'local'}</td>
                    <td>{m.data_handling.regions.join(', ') || '—'}</td>
                    <td className="text-xs text-ink-500">
                      q {m.quality_prior} · s {m.speed_prior}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Spinner />
          ))}
        {tab === 'tools' &&
          (tools.data ? (
            <div className="grid gap-3 md:grid-cols-2">
              {tools.data.map((t) => (
                <Card key={t.name} title={<span className="mono">{t.name}@{t.version}</span>} actions={<Badge tone={t.side_effect === 'none' ? 'neutral' : t.side_effect === 'external_write' ? 'red' : 'amber'}>{t.side_effect}</Badge>}>
                  <p className="text-sm text-ink-700">{t.description}</p>
                  <div className="mt-2 text-xs text-ink-500">
                    permissions: {t.permissions.join(', ') || 'none'} · retry {t.retry} · limits {JSON.stringify(t.limits)}
                  </div>
                  <details className="mt-2 text-xs">
                    <summary className="cursor-pointer text-ink-500">input schema</summary>
                    <Json value={t.input_schema} />
                  </details>
                </Card>
              ))}
            </div>
          ) : (
            <Spinner />
          ))}
        {tab === 'skills' &&
          (skills.data ? (
            skills.data.length ? (
              <div className="space-y-3">
                {skills.data.map((s) => (
                  <SkillCard key={`${s.name}@${s.version}`} skill={s} />
                ))}
              </div>
            ) : (
              <p className="text-sm text-ink-500">No skills installed on the server (HAWSER_SKILL_PATHS).</p>
            )
          ) : (
            <Spinner />
          ))}
      </div>
    </div>
  )
}

function SkillCard({ skill }: { skill: SkillManifest }) {
  return (
    <Card title={<span className="mono">{skill.name}@{skill.version}</span>} actions={<Badge tone="blue">{skill.mode}</Badge>}>
      <p className="text-sm text-ink-700">{skill.description}</p>
      <div className="mt-2 grid gap-2 text-xs md:grid-cols-3">
        <div>
          <div className="label">inputs</div>
          {Object.entries(skill.inputs).map(([k, v]) => (
            <div key={k} className="mono">
              {k}: {v.type}
              {v.required === false ? ' (optional)' : ''}
            </div>
          ))}
        </div>
        <div>
          <div className="label">tools (permissions)</div>
          {skill.tools.length ? skill.tools.map((t) => <div key={t} className="mono">{t}</div>) : <span className="text-ink-400">none</span>}
        </div>
        <div>
          <div className="label">validators</div>
          {skill.validators.map((v, i) => (
            <div key={i} className="mono">
              {typeof v === 'string' ? v : JSON.stringify(v)}
            </div>
          ))}
          <div className="mt-1 text-ink-500">evidence: {JSON.stringify(skill.evidence)}</div>
        </div>
      </div>
      <details className="mt-2 text-xs">
        <summary className="cursor-pointer text-ink-500">manifest</summary>
        <Json value={skill} />
      </details>
    </Card>
  )
}
