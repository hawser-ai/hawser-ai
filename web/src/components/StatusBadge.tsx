import { Badge } from './ui'

const TONES: Record<string, 'neutral' | 'green' | 'amber' | 'red' | 'blue' | 'violet'> = {
  accepted: 'green',
  needs_review: 'amber',
  awaiting_approval: 'violet',
  rejected: 'red',
  failed: 'red',
  cancelled: 'neutral',
  queued: 'neutral',
  admitting: 'blue',
  planning: 'blue',
  running: 'blue',
  validating: 'blue',
  repairing: 'blue',
  completed: 'green',
  pending: 'neutral',
  skipped: 'neutral',
  open: 'amber',
  approved: 'green',
  delivered: 'green',
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge tone={TONES[status] ?? 'neutral'}>
      <span data-testid="status">{status.replace(/_/g, ' ')}</span>
    </Badge>
  )
}
