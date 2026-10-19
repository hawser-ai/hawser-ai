import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api } from '@/api/client'
import type { Health } from '@/api/models'
import { ErrorBox } from '@/components/ui'
import { useAuth } from '@/state/auth'

export function Login() {
  const { baseUrl, signIn } = useAuth()
  const [url, setUrl] = useState(baseUrl)
  const [key, setKey] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()
  const location = useLocation() as { state?: { from?: string } }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    signIn(url, key.trim())
    try {
      await api<Health>('/readyz')
      await api('/v1/runs?limit=1')
      navigate(location.state?.from ?? '/playground', { replace: true })
    } catch (err) {
      setError(err)
      useAuth.getState().signOut()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid h-full place-items-center bg-ink-50 px-4">
      <form onSubmit={submit} className="card w-full max-w-md p-6">
        <div className="mb-4 flex items-center gap-2">
          <span className="grid h-8 w-8 place-items-center rounded bg-ink-900 text-sm font-bold text-white">H</span>
          <div>
            <h1 className="text-lg font-semibold">Sign in to Hawser</h1>
            <p className="text-xs text-ink-500">An API key from <code className="mono">hawserd admin create-key</code> (or the dev key).</p>
          </div>
        </div>
        <label className="block">
          <span className="label">Server</span>
          <input className="input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://localhost:8080 (empty = same origin)" />
        </label>
        <label className="mt-3 block">
          <span className="label">API key</span>
          <input className="input" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder="hk_live_…" required data-testid="api-key" />
        </label>
        {error ? <div className="mt-3"><ErrorBox error={error} /></div> : null}
        <button className="btn-primary mt-4 w-full justify-center" disabled={busy || !key.trim()} type="submit">
          {busy ? 'Checking…' : 'Sign in'}
        </button>
        <p className="mt-3 text-[11px] text-ink-400">The key stays in this browser session only.</p>
      </form>
    </div>
  )
}
