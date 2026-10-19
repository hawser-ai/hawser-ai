import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'

/** Where the API lives when the app is served by hawserd itself (same origin) or by `vite dev`. */
export function defaultBaseUrl(): string {
  if (typeof window === 'undefined') return 'http://127.0.0.1:8080'
  const { origin, pathname } = window.location
  if (pathname.startsWith('/app')) return origin
  return import.meta.env.VITE_HAWSER_SERVER_URL ?? ''
}

interface AuthState {
  baseUrl: string
  apiKey: string | null
  tenantId: string | null
  signIn: (baseUrl: string, apiKey: string) => void
  signOut: () => void
}

/** The API key is kept for the browser session only (sessionStorage), never in localStorage. */
export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      baseUrl: defaultBaseUrl(),
      apiKey: null,
      tenantId: null,
      signIn: (baseUrl, apiKey) => set({ baseUrl: baseUrl.replace(/\/+$/, ''), apiKey }),
      signOut: () => set({ apiKey: null, tenantId: null }),
    }),
    {
      name: 'hawser.session',
      storage: createJSONStorage(() => (typeof window === 'undefined' ? memoryStorage : window.sessionStorage)),
    },
  ),
)

const memoryStorage: Storage = {
  length: 0,
  clear: () => undefined,
  getItem: () => null,
  key: () => null,
  removeItem: () => undefined,
  setItem: () => undefined,
}
