import { useEffect, useState } from 'react'
import { fetchBlobUrl } from '@/api/client'

/** Loads one rendered page with credentials and revokes the object URL on change. */
export function usePageImage(assetId: string | undefined, page: number, dpi = 110) {
  const [url, setUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    if (!assetId) return
    const ctrl = new AbortController()
    let current: string | null = null
    setLoading(true)
    fetchBlobUrl(`/v1/assets/${assetId}/pages/${page}?dpi=${dpi}`, ctrl.signal)
      .then((r) => {
        current = r.url
        setUrl(r.url)
      })
      .catch(() => setUrl(null))
      .finally(() => setLoading(false))
    return () => {
      ctrl.abort()
      if (current) URL.revokeObjectURL(current)
    }
  }, [assetId, page, dpi])
  return { url, loading }
}
