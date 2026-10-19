export function usd(value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined) return '—'
  return `$${value.toFixed(digits)}`
}

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

export function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  if (value < 1000) return `${Math.round(value)} ms`
  return `${(value / 1000).toFixed(1)} s`
}

export function when(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function clock(iso: string): string {
  const d = new Date(iso)
  return `${d.toLocaleTimeString(undefined, { hour12: false })}.${String(d.getMilliseconds()).padStart(3, '0')}`
}

export function shortId(id: string, keep = 8): string {
  const [prefix, rest] = id.split('_', 2)
  return rest ? `${prefix}_…${rest.slice(-keep)}` : id
}

export function truncate(text: string, width = 60): string {
  return text.length <= width ? text : `${text.slice(0, width - 1)}…`
}

export function metricLabel(key: string): string {
  const labels: Record<string, string> = {
    field_accuracy: 'Field accuracy',
    doc_pass_rate: 'Document pass rate',
    status_accuracy: 'Status accuracy',
    review_rate: 'Review rate',
    failed_rate: 'Failed rate',
    accepted_rate: 'Accepted rate',
    rejected_rate: 'Rejected rate',
    evidence_coverage: 'Evidence coverage',
    unknown_precision: 'Unknown precision',
    unknown_recall: 'Unknown recall',
    cost_total_usd: 'Total cost',
    cost_mean_usd: 'Cost / case (mean)',
    cost_p50_usd: 'Cost / case (p50)',
    cost_p95_usd: 'Cost / case (p95)',
    latency_p50_ms: 'Latency p50',
    latency_p95_ms: 'Latency p95',
    model_calls_mean: 'Model calls / case',
    repair_attempts_mean: 'Repair attempts / case',
    cases: 'Cases',
    errors: 'Errors',
  }
  return labels[key] ?? key
}

export function formatMetric(key: string, value: number): string {
  if (key.endsWith('_rate') || ['field_accuracy', 'evidence_coverage', 'unknown_precision', 'unknown_recall', 'status_accuracy'].includes(key))
    return pct(value)
  if (key.endsWith('_usd')) return usd(value)
  if (key.endsWith('_ms')) return ms(value)
  if (key === 'cases' || key === 'errors') return String(Math.round(value))
  return value.toFixed(2)
}

/** Higher is better for these; lower is better for cost, latency, failed/review rate, repairs. */
export function higherIsBetter(key: string): boolean | null {
  if (['field_accuracy', 'doc_pass_rate', 'status_accuracy', 'evidence_coverage', 'unknown_precision', 'unknown_recall', 'accepted_rate'].includes(key)) return true
  if (key.endsWith('_usd') || key.endsWith('_ms') || ['failed_rate', 'review_rate', 'rejected_rate', 'repair_attempts_mean', 'model_calls_mean', 'errors'].includes(key)) return false
  return null
}
