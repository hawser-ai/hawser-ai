import clsx from 'clsx'
import { useRef, useState } from 'react'
import { useUpload } from '@/api/hooks'
import type { Asset } from '@/api/models'
import { Badge, ErrorBox } from './ui'

interface Props {
  assets: Asset[]
  onChange: (assets: Asset[]) => void
}

/** Drag-and-drop upload; each file is admitted by the server and its manifest shown inline. */
export function FileDrop({ assets, onChange }: Props) {
  const upload = useUpload()
  const [over, setOver] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const input = useRef<HTMLInputElement>(null)

  const add = async (files: FileList | File[]) => {
    setError(null)
    const added: Asset[] = []
    for (const f of Array.from(files)) {
      try {
        added.push(await upload.mutateAsync(f))
      } catch (e) {
        setError(e)
      }
    }
    if (added.length) onChange([...assets, ...added])
  }

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        aria-label="upload documents"
        onClick={() => input.current?.click()}
        onKeyDown={(e) => e.key === 'Enter' && input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          void add(e.dataTransfer.files)
        }}
        className={clsx(
          'flex cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed px-4 py-6 text-sm',
          over ? 'border-accent-500 bg-accent-50' : 'border-ink-300 bg-white hover:border-ink-400',
        )}
      >
        <span className="font-medium text-ink-800">Drop PDFs, images, text or JSON here</span>
        <span className="text-xs text-ink-500">or click to choose · each file is admitted and probed before any model sees it</span>
        <input ref={input} type="file" multiple className="hidden" data-testid="file-input" onChange={(e) => e.target.files && void add(e.target.files)} />
      </div>
      {upload.isPending && <div className="mt-2 text-xs text-ink-500">admitting…</div>}
      {error ? <div className="mt-2"><ErrorBox error={error} /></div> : null}
      {assets.length > 0 && (
        <ul className="mt-2 divide-y divide-ink-100 rounded border border-ink-200 text-sm">
          {assets.map((a) => (
            <li key={a.id} className="flex flex-wrap items-center gap-2 px-3 py-2" data-testid="asset-row">
              <span className="font-medium text-ink-900">{a.name ?? a.id}</span>
              <Badge tone="blue">{a.media_class}</Badge>
              <span className="text-xs text-ink-500">{(a.size_bytes / 1024).toFixed(0)} KB</span>
              {a.manifest.pdf && <span className="text-xs text-ink-500">{a.manifest.pdf.page_count} page{a.manifest.pdf.page_count === 1 ? '' : 's'}</span>}
              {a.manifest.image && (
                <span className="text-xs text-ink-500">
                  {a.manifest.image.width}×{a.manifest.image.height}
                </span>
              )}
              {a.manifest.execution_classes.map((c) => (
                <Badge key={c} tone={c.includes('ocr') ? 'amber' : 'neutral'}>
                  {c}
                </Badge>
              ))}
              {a.manifest.warnings.map((w) => (
                <span key={w} className="text-xs text-amber-700">
                  {w}
                </span>
              ))}
              <span className="ml-auto text-[10px] text-ink-400">
                limits: {a.manifest.limits.max_pages ?? '∞'} pages · {(a.manifest.limits.max_bytes / 1048576).toFixed(0)} MB
              </span>
              <button type="button" className="btn-ghost text-xs" onClick={() => onChange(assets.filter((x) => x.id !== a.id))} aria-label={`remove ${a.name ?? a.id}`}>
                remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
