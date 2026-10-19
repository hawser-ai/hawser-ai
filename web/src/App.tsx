import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Navigate, Outlet, Route, BrowserRouter, Routes, useLocation } from 'react-router-dom'
import { Layout } from '@/components/Layout'
import { EvalCompare, EvalDetail, Evals } from '@/pages/Evals'
import { Login } from '@/pages/Login'
import { Playground } from '@/pages/Playground'
import { Policy } from '@/pages/Policy'
import { Registry } from '@/pages/Registry'
import { Review } from '@/pages/Review'
import { Reviews } from '@/pages/Reviews'
import { RunDetail } from '@/pages/RunDetail'
import { Runs } from '@/pages/Runs'
import { Settings } from '@/pages/Settings'
import { Usage } from '@/pages/Usage'
import { useAuth } from '@/state/auth'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 2000 } },
})

function RequireAuth() {
  const apiKey = useAuth((s) => s.apiKey)
  const location = useLocation()
  if (!apiKey) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return <Outlet />
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RequireAuth />}>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/playground" replace />} />
          <Route path="/playground" element={<Playground />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:id" element={<RunDetail />} />
          <Route path="/runs/:id/review" element={<Review />} />
          <Route path="/reviews" element={<Reviews />} />
          <Route path="/evals" element={<Evals />} />
          <Route path="/evals/compare" element={<EvalCompare />} />
          <Route path="/evals/:id" element={<EvalDetail />} />
          <Route path="/registry" element={<Registry />} />
          <Route path="/usage" element={<Usage />} />
          <Route path="/policy" element={<Policy />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/playground" replace />} />
        </Route>
      </Route>
    </Routes>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename="/app">
        <AppRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  )
}
