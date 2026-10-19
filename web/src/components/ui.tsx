import clsx from 'clsx'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={clsx('card', className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between border-b border-ink-100 px-4 py-2.5">
          <h2 className="text-sm font-semibold text-ink-800">{title}</h2>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

export function Badge({ children, tone = 'neutral', className }: { children: ReactNode; tone?: 'neutral' | 'green' | 'amber' | 'red' | 'blue' | 'violet'; className?: string }) {
  const tones = {
    neutral: 'bg-ink-100 text-ink-700 ring-ink-200',
    green: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
    amber: 'bg-amber-50 text-amber-800 ring-amber-200',
    red: 'bg-red-50 text-red-700 ring-red-200',
    blue: 'bg-accent-50 text-accent-700 ring-accent-200',
    violet: 'bg-violet-50 text-violet-700 ring-violet-200',
  }
  return (
    <span className={clsx('inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset', tones[tone], className)}>
      {children}
    </span>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-md border border-dashed border-ink-300 p-6 text-center text-sm text-ink-500">{children}</div>
}

export function ErrorBox({ error }: { error: unknown }) {
  const e = error as { message?: string; code?: string; requestId?: string | null }
  return (
    <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
      <span className="font-semibold">{e.code ?? 'error'}</span>: {e.message ?? String(error)}
      {e.requestId && <span className="ml-2 text-xs text-red-500">request {e.requestId}</span>}
    </div>
  )
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-ink-500" role="status">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-ink-300 border-t-accent-600" />
      {label}
    </div>
  )
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: ReactNode }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div role="tablist" className="flex gap-1 border-b border-ink-200">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={t.id === value}
          onClick={() => onChange(t.id)}
          className={clsx(
            '-mb-px border-b-2 px-3 py-2 text-sm font-medium',
            t.id === value ? 'border-accent-600 text-accent-700' : 'border-transparent text-ink-500 hover:text-ink-800',
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  )
}

export function KeyValue({ items }: { items: { k: ReactNode; v: ReactNode }[] }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
      {items.map((it, i) => (
        <div key={i} className="contents">
          <dt className="text-ink-500">{it.k}</dt>
          <dd className="min-w-0 break-words text-ink-900">{it.v}</dd>
        </div>
      ))}
    </dl>
  )
}

export function RunLink({ id, children }: { id: string; children?: ReactNode }) {
  return (
    <Link to={`/runs/${id}`} className="mono text-accent-700 hover:underline">
      {children ?? id}
    </Link>
  )
}

export function Json({ value, className }: { value: unknown; className?: string }) {
  return <pre className={clsx('mono max-h-96 overflow-auto rounded bg-ink-50 p-2 text-ink-800', className)}>{JSON.stringify(value, null, 2)}</pre>
}

export function Field({ label, children, hint }: { label: ReactNode; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-ink-500">{hint}</span>}
    </label>
  )
}
