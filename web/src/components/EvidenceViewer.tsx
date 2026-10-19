import { useEffect, useMemo, useState } from 'react'
import { useAsset } from '@/api/hooks'
import type { Evidence, RunResult } from '@/api/models'
import { evidenceBoxes, pageSizePt, pagesWithEvidence, type Box } from '@/lib/evidence'
import { usePageImage } from '@/lib/pageImage'
import { EvidenceOverlay } from './EvidenceOverlay'
import { Empty } from './ui'

interface Props {
  result: RunResult
  selectedPointer: string | null
  onSelectPointer: (pointer: string | null) => void
}

/**
 * Page navigation + overlay + the list of evidence on the page. Selecting a field elsewhere
 * (the result tree) jumps to its page and highlights its box; selecting a box selects the field.
 */
export function EvidenceViewer({ result, selectedPointer, onSelectPointer }: Props) {
  const boxes = useMemo(() => evidenceBoxes(result), [result])
  const pages = useMemo(() => pagesWithEvidence(boxes), [boxes])
  const assetIds = useMemo(() => [...new Set(boxes.map((b) => evidenceAsset(result, b.evidenceId)).filter(Boolean))] as string[], [boxes, result])
  const [assetId, setAssetId] = useState<string | undefined>(assetIds[0])
  const [page, setPage] = useState<number>(pages[0] ?? 1)
  const asset = useAsset(assetId)
  const pageCount = asset.data?.manifest.pdf?.page_count ?? 1
  const { url, loading } = usePageImage(assetId, page)

  const selectedBox = useMemo(
    () => boxes.find((b) => b.pointer === selectedPointer && assetOf(result, b.evidenceId) === assetId) ?? null,
    [boxes, selectedPointer, assetId, result],
  )
  useEffect(() => {
    if (!assetIds.length) return
    if (!assetId || !assetIds.includes(assetId)) setAssetId(assetIds[0])
  }, [assetIds, assetId])
  useEffect(() => {
    if (!selectedPointer) return
    const target = boxes.find((b) => b.pointer === selectedPointer)
    if (!target) return
    const a = assetOf(result, target.evidenceId)
    if (a && a !== assetId) setAssetId(a)
    setPage(target.page)
  }, [selectedPointer]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!boxes.length) return <Empty>No region evidence on this result. Calculation and page-level evidence appear in the Fields table.</Empty>

  const pageBoxes = boxes.filter((b) => b.page === page && assetOf(result, b.evidenceId) === assetId)
  const size = asset.data?.media_class === 'image' && asset.data.manifest.image
    ? { width: asset.data.manifest.image.width, height: asset.data.manifest.image.height }
    : pageSizePt(asset.data, page)

  return (
    <div className="flex flex-col gap-3 lg:flex-row" data-testid="evidence-viewer">
      <div className="min-w-0 flex-1">
        <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
          {assetIds.length > 1 && (
            <select className="input w-auto" value={assetId} onChange={(e) => setAssetId(e.target.value)} aria-label="document">
              {assetIds.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          )}
          <div className="flex items-center gap-1">
            <button className="btn-secondary" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="previous page">
              ‹
            </button>
            <span className="px-1 text-ink-600">
              page {page} / {pageCount}
            </span>
            <button className="btn-secondary" disabled={page >= pageCount} onClick={() => setPage(page + 1)} aria-label="next page">
              ›
            </button>
          </div>
          <span className="text-xs text-ink-500">
            {pageBoxes.length} region{pageBoxes.length === 1 ? '' : 's'} on this page · pages with evidence: {pages.join(', ')}
          </span>
        </div>
        <div className="overflow-auto rounded border border-ink-200 bg-ink-100 p-2">
          <EvidenceOverlay
            imageUrl={url}
            pageSize={size}
            boxes={pageBoxes}
            selectedId={selectedBox?.evidenceId ?? null}
            onSelect={(b: Box | null) => onSelectPointer(b?.pointer ?? null)}
            loading={loading}
          />
        </div>
      </div>
      <aside className="w-full shrink-0 lg:w-72">
        <h3 className="label">Evidence on page {page}</h3>
        <ul className="max-h-[32rem] divide-y divide-ink-100 overflow-auto rounded border border-ink-200 text-xs">
          {pageBoxes.map((b) => (
            <li key={b.evidenceId}>
              <button
                type="button"
                className={`block w-full px-2 py-1.5 text-left hover:bg-ink-50 ${b.pointer === selectedPointer ? 'bg-amber-50' : ''}`}
                onClick={() => onSelectPointer(b.pointer)}
              >
                <div className="mono text-ink-800">{b.pointer}</div>
                <div className="truncate text-ink-500">“{b.text}”</div>
              </button>
            </li>
          ))}
        </ul>
      </aside>
    </div>
  )
}

function evidenceAsset(result: RunResult, evidenceId: string): string | null {
  return assetOf(result, evidenceId)
}

function assetOf(result: RunResult, evidenceId: string): string | null {
  const e: Evidence | undefined = result.evidence.find((x) => x.id === evidenceId)
  if (!e) return null
  return 'asset_id' in e ? (e.asset_id ?? null) : null
}
