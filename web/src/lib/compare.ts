import type { EvalReport } from '@/api/models'
import { higherIsBetter } from './format'

export interface MetricDelta {
  key: string
  baseline: number
  candidate: number
  delta: number
  verdict: 'better' | 'worse' | 'same' | 'neutral'
}

export interface CaseChange {
  caseId: string
  baseline: { passed: boolean; status: string; accuracy: number } | null
  candidate: { passed: boolean; status: string; accuracy: number } | null
  kind: 'regression' | 'improvement' | 'unchanged' | 'added' | 'removed'
}

export interface Comparison {
  metrics: MetricDelta[]
  cases: CaseChange[]
  regressions: CaseChange[]
  improvements: CaseChange[]
}

const EPS = 1e-9

/** Mirrors hawser_evals.compare: metric deltas plus per-case regressions and improvements. */
export function compareReports(baseline: EvalReport, candidate: EvalReport): Comparison {
  const keys = [...new Set([...Object.keys(baseline.metrics), ...Object.keys(candidate.metrics)])].sort()
  const metrics: MetricDelta[] = keys.map((key) => {
    const a = baseline.metrics[key] ?? 0
    const b = candidate.metrics[key] ?? 0
    const delta = b - a
    const dir = higherIsBetter(key)
    let verdict: MetricDelta['verdict'] = 'neutral'
    if (Math.abs(delta) < EPS) verdict = 'same'
    else if (dir === true) verdict = delta > 0 ? 'better' : 'worse'
    else if (dir === false) verdict = delta < 0 ? 'better' : 'worse'
    return { key, baseline: a, candidate: b, delta, verdict }
  })
  const base = new Map(baseline.cases.map((c) => [c.case_id, c]))
  const cand = new Map(candidate.cases.map((c) => [c.case_id, c]))
  const ids = [...new Set([...base.keys(), ...cand.keys()])].sort()
  const cases: CaseChange[] = ids.map((id) => {
    const a = base.get(id)
    const b = cand.get(id)
    const pick = (c: typeof a) => (c ? { passed: c.passed, status: c.status, accuracy: c.total ? c.correct / c.total : 0 } : null)
    let kind: CaseChange['kind'] = 'unchanged'
    if (a && !b) kind = 'removed'
    else if (!a && b) kind = 'added'
    else if (a && b) {
      if (a.passed && !b.passed) kind = 'regression'
      else if (!a.passed && b.passed) kind = 'improvement'
      else if (a.total && b.total && b.correct / b.total < a.correct / a.total - EPS) kind = 'regression'
      else if (a.total && b.total && b.correct / b.total > a.correct / a.total + EPS) kind = 'improvement'
    }
    return { caseId: id, baseline: pick(a), candidate: pick(b), kind }
  })
  return {
    metrics,
    cases,
    regressions: cases.filter((c) => c.kind === 'regression'),
    improvements: cases.filter((c) => c.kind === 'improvement'),
  }
}
