import type { Usage } from '@/api/models'
import { ms, usd } from '@/lib/format'

export function UsageBadge({ usage, maxCost }: { usage: Usage; maxCost?: number | null }) {
  const t = usage.totals
  const replayed = usage.model_calls.some((c) => c.replayed)
  const share = maxCost ? Math.min(100, (t.cost_usd / maxCost) * 100) : null
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-600">
      <span>
        <b className="text-ink-900">{t.model_calls}</b> model call{t.model_calls === 1 ? '' : 's'}
      </span>
      <span>
        {t.input_tokens.toLocaleString()} in / {t.output_tokens.toLocaleString()} out
      </span>
      <span>
        <b className="text-ink-900">{usd(t.cost_usd)}</b>
        {replayed && <span className="ml-1 text-ink-400">(replayed, not billed)</span>}
      </span>
      <span>{ms(t.wall_time_ms)}</span>
      {share !== null && (
        <span className="flex items-center gap-1" title={`${usd(t.cost_usd)} of ${usd(maxCost)} budget`}>
          <span className="h-1.5 w-20 overflow-hidden rounded bg-ink-200">
            <span className={share > 90 ? 'block h-full bg-red-500' : 'block h-full bg-accent-500'} style={{ width: `${share}%` }} />
          </span>
          {share.toFixed(0)}% of budget
        </span>
      )}
    </div>
  )
}
