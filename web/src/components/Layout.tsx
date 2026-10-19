import clsx from 'clsx'
import { Activity, BarChart3, BookOpen, FlaskConical, ListChecks, LogOut, Play, Settings2, ShieldCheck } from 'lucide-react'
import { NavLink, Outlet } from 'react-router-dom'
import { useHealth } from '@/api/hooks'
import { useAuth } from '@/state/auth'

const NAV = [
  { to: '/playground', label: 'Playground', icon: Play },
  { to: '/runs', label: 'Runs', icon: Activity },
  { to: '/reviews', label: 'Reviews', icon: ListChecks },
  { to: '/evals', label: 'Evaluations', icon: FlaskConical },
  { to: '/registry', label: 'Registry', icon: BookOpen },
  { to: '/usage', label: 'Usage', icon: BarChart3 },
  { to: '/policy', label: 'Policy', icon: ShieldCheck },
]

export function Layout() {
  const { baseUrl, signOut } = useAuth()
  const health = useHealth()
  return (
    <div className="flex h-full">
      <aside className="flex w-56 shrink-0 flex-col border-r border-ink-200 bg-white">
        <div className="flex items-center gap-2 px-4 py-4">
          <span className="grid h-7 w-7 place-items-center rounded bg-ink-900 text-xs font-bold text-white">H</span>
          <div>
            <div className="text-sm font-semibold text-ink-900">Hawser</div>
            <div className="text-[10px] text-ink-500">verifiable workflows</div>
          </div>
        </div>
        <nav className="flex-1 px-2">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                clsx('mb-0.5 flex items-center gap-2 rounded-md px-2 py-1.5 text-sm', isActive ? 'bg-ink-100 font-medium text-ink-900' : 'text-ink-600 hover:bg-ink-50 hover:text-ink-900')
              }
            >
              <Icon size={16} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-ink-100 px-3 py-3 text-xs text-ink-500">
          <div className="flex items-center gap-1.5">
            <span className={clsx('h-2 w-2 rounded-full', health.data?.status === 'ready' ? 'bg-emerald-500' : 'bg-red-500')} />
            <span className="truncate" title={baseUrl}>
              {baseUrl || 'same origin'}
            </span>
          </div>
          {health.data && (
            <div className="mt-1 text-[10px] text-ink-400">
              hawserd {health.data.version} · jobs {Object.entries(health.data.jobs).map(([k, v]) => `${k} ${v}`).join(', ') || 'idle'}
            </div>
          )}
          <button type="button" className="btn-ghost mt-2 w-full justify-start px-1 text-xs" onClick={signOut}>
            <LogOut size={14} /> sign out
          </button>
          <NavLink to="/settings" className="mt-1 flex items-center gap-1 text-[11px] text-ink-400 hover:text-ink-700">
            <Settings2 size={12} /> webhooks & settings
          </NavLink>
        </div>
      </aside>
      <main className="min-w-0 flex-1 overflow-auto">
        <div className="mx-auto max-w-7xl px-6 py-5">
          <Outlet />
        </div>
      </main>
    </div>
  )
}

export function PageHeader({ title, subtitle, actions }: { title: React.ReactNode; subtitle?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-ink-900">{title}</h1>
        {subtitle && <p className="text-sm text-ink-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </header>
  )
}
