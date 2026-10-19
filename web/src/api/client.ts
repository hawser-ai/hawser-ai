import { useAuth } from '@/state/auth'
import type { Event } from './models'

/** A problem+json error from hawserd (or a transport failure). */
export class ApiError extends Error {
  status: number
  code: string
  errorType: string | null
  details: Record<string, unknown>
  requestId: string | null

  constructor(status: number, code: string, message: string, extra: Partial<ApiError> = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.errorType = extra.errorType ?? null
    this.details = extra.details ?? {}
    this.requestId = extra.requestId ?? null
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  body?: unknown
  headers?: Record<string, string>
  signal?: AbortSignal
}

function credentials(): { baseUrl: string; apiKey: string | null } {
  const { baseUrl, apiKey } = useAuth.getState()
  return { baseUrl, apiKey }
}

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const { apiKey } = credentials()
  return apiKey ? { Authorization: `Bearer ${apiKey}`, ...extra } : extra
}

async function toError(res: Response): Promise<ApiError> {
  let body: Record<string, unknown> = {}
  try {
    body = (await res.json()) as Record<string, unknown>
  } catch {
    /* not JSON */
  }
  const code = typeof body.code === 'string' ? body.code : `http_${res.status}`
  const detail = typeof body.detail === 'string' ? body.detail : res.statusText || `HTTP ${res.status}`
  return new ApiError(res.status, code, detail, {
    errorType: typeof body.error_type === 'string' ? body.error_type : null,
    details: (body.details as Record<string, unknown>) ?? {},
    requestId: res.headers.get('X-Hawser-Request-Id'),
  })
}

/** JSON request against the configured server; throws `ApiError` on non-2xx. */
export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { baseUrl } = credentials()
  const res = await fetch(baseUrl + path, {
    method: opts.method ?? 'GET',
    headers: authHeaders({
      ...(opts.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(opts.headers ?? {}),
    }),
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    signal: opts.signal,
  })
  if (!res.ok) throw await toError(res)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/** Same as `api`, but also returns a header (ETag, Location). */
export async function apiWithHeaders<T>(
  path: string,
  opts: RequestOptions = {},
): Promise<{ data: T; headers: Headers }> {
  const { baseUrl } = credentials()
  const res = await fetch(baseUrl + path, {
    method: opts.method ?? 'GET',
    headers: authHeaders({
      ...(opts.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(opts.headers ?? {}),
    }),
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  })
  if (!res.ok) throw await toError(res)
  return { data: (await res.json()) as T, headers: res.headers }
}

/** Multipart upload of one file to POST /v1/assets. */
export async function uploadAsset<T>(file: File, name?: string): Promise<T> {
  const { baseUrl } = credentials()
  const form = new FormData()
  form.append('file', file, name ?? file.name)
  form.append('name', name ?? file.name)
  const res = await fetch(baseUrl + '/v1/assets', { method: 'POST', headers: authHeaders(), body: form })
  if (!res.ok) throw await toError(res)
  return (await res.json()) as T
}

/** Fetch binary content with the API key and hand back an object URL (caller revokes). */
export async function fetchBlobUrl(path: string, signal?: AbortSignal): Promise<{ url: string; headers: Headers }> {
  const { baseUrl } = credentials()
  const res = await fetch(baseUrl + path, { headers: authHeaders(), signal })
  if (!res.ok) throw await toError(res)
  const blob = await res.blob()
  return { url: URL.createObjectURL(blob), headers: res.headers }
}

/**
 * Follow a run's events over SSE until the server sends `stream.end`.
 * `EventSource` cannot carry an Authorization header, so this parses the stream by hand.
 */
export async function streamRunEvents(
  runId: string,
  onEvent: (event: Event) => void,
  opts: { afterSeq?: number; signal?: AbortSignal } = {},
): Promise<{ status: string | null }> {
  const { baseUrl } = credentials()
  const headers = authHeaders({ Accept: 'text/event-stream' })
  if (opts.afterSeq) headers['Last-Event-ID'] = String(opts.afterSeq)
  const res = await fetch(`${baseUrl}/v1/runs/${runId}/events?stream=true`, { headers, signal: opts.signal })
  if (!res.ok || !res.body) throw await toError(res)
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventType: string | null = null
  let data: string[] = []
  let endStatus: string | null = null
  const dispatch = (): boolean => {
    if (!data.length) return false
    const payload = data.join('\n')
    const type = eventType
    eventType = null
    data = []
    if (type === 'stream.end') {
      try {
        endStatus = (JSON.parse(payload) as { status: string | null }).status
      } catch {
        endStatus = null
      }
      return true
    }
    try {
      onEvent(JSON.parse(payload) as Event)
    } catch {
      /* ignore malformed frames */
    }
    return false
  }
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, idx).replace(/\r$/, '')
      buffer = buffer.slice(idx + 1)
      if (line === '') {
        if (dispatch()) return { status: endStatus }
        continue
      }
      if (line.startsWith(':')) continue
      const sep = line.indexOf(':')
      const field = sep >= 0 ? line.slice(0, sep) : line
      const value = sep >= 0 ? line.slice(sep + 1).replace(/^ /, '') : ''
      if (field === 'event') eventType = value
      else if (field === 'data') data.push(value)
    }
  }
  dispatch()
  return { status: endStatus }
}
