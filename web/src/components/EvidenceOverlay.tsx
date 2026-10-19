import clsx from 'clsx'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { Box } from '@/lib/evidence'
import { boxStyle } from '@/lib/evidence'

export interface OverlayProps {
  /** An image URL (object URL fetched with credentials) for the page. */
  imageUrl: string | null
  /** The page's size in the evidence coordinate system (PDF points, or pixels for images). */
  pageSize: { width: number; height: number } | null
  boxes: Box[]
  selectedId: string | null
  onSelect: (box: Box | null) => void
  onHover?: (box: Box | null) => void
  loading?: boolean
}

/**
 * The page with evidence regions drawn on top. Boxes are positioned as percentages of the
 * page size, so any render dpi works. Boxes are focusable: Tab reaches them, arrow keys move
 * between them in reading order, Enter/Space selects, Escape clears.
 */
export function EvidenceOverlay({ imageUrl, pageSize, boxes, selectedId, onSelect, onHover, loading }: OverlayProps) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const [hover, setHover] = useState<Box | null>(null)
  const ordered = [...boxes].sort((a, b) => a.bbox.y0 - b.bbox.y0 || a.bbox.x0 - b.bbox.x0)

  useEffect(() => {
    if (!selectedId) return
    const i = ordered.findIndex((b) => b.evidenceId === selectedId)
    refs.current[i]?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [selectedId]) // eslint-disable-line react-hooks/exhaustive-deps

  const onKey = useCallback(
    (e: React.KeyboardEvent, i: number) => {
      const move = (j: number) => {
        e.preventDefault()
        const target = refs.current[(j + ordered.length) % ordered.length]
        target?.focus()
      }
      if (e.key === 'ArrowDown' || e.key === 'ArrowRight') move(i + 1)
      else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') move(i - 1)
      else if (e.key === 'Home') move(0)
      else if (e.key === 'End') move(ordered.length - 1)
      else if (e.key === 'Escape') {
        e.preventDefault()
        onSelect(null)
      }
    },
    [ordered.length, onSelect],
  )

  return (
    <div className="relative inline-block max-w-full select-none" data-testid="evidence-overlay">
      {imageUrl ? (
        <img src={imageUrl} alt="document page" className="block max-w-full" draggable={false} />
      ) : (
        <div className="flex h-96 w-72 items-center justify-center bg-ink-100 text-sm text-ink-500">{loading ? 'Rendering page…' : 'No page'}</div>
      )}
      {pageSize &&
        ordered.map((b, i) => (
          <button
            key={b.evidenceId}
            ref={(el) => {
              refs.current[i] = el
            }}
            type="button"
            className="evidence-box"
            style={boxStyle(b.bbox, pageSize)}
            data-selected={b.evidenceId === selectedId}
            data-evidence-id={b.evidenceId}
            data-pointer={b.pointer ?? undefined}
            aria-label={`${b.pointer ?? 'evidence'}: ${b.text ?? ''}`}
            aria-pressed={b.evidenceId === selectedId}
            title={`${b.pointer ?? ''} ${b.text ?? ''}`}
            onClick={() => onSelect(b.evidenceId === selectedId ? null : b)}
            onKeyDown={(e) => onKey(e, i)}
            onMouseEnter={() => {
              setHover(b)
              onHover?.(b)
            }}
            onMouseLeave={() => {
              setHover(null)
              onHover?.(null)
            }}
            onFocus={() => setHover(b)}
            onBlur={() => setHover(null)}
          />
        ))}
      {hover && pageSize && (
        <div
          role="tooltip"
          className={clsx('pointer-events-none absolute z-10 max-w-xs rounded bg-ink-900/95 px-2 py-1 text-xs text-white shadow-lg')}
          style={{
            left: `${Math.min(85, (hover.bbox.x0 / pageSize.width) * 100)}%`,
            top: `calc(${(hover.bbox.y1 / pageSize.height) * 100}% + 4px)`,
          }}
        >
          <div className="font-semibold">{hover.pointer ?? 'evidence'}</div>
          {hover.text && <div className="mono mt-0.5 text-[11px] text-ink-100">“{hover.text}”</div>}
          <div className="mt-0.5 text-[10px] text-ink-300">
            {hover.producer}
            {hover.confidence !== null && ` · confidence ${hover.confidence.toFixed(2)}`}
          </div>
        </div>
      )}
    </div>
  )
}
