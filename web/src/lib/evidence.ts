import type { Asset, BBox, Evidence, FieldMeta, Issue, RunResult } from '@/api/models'
import { getPointer } from './json'

export interface FieldRow {
  pointer: string
  value: unknown
  meta: FieldMeta
  evidence: Evidence[]
  issues: Issue[]
}

/** One row per field in `field_meta`, with its value, evidence objects and issues resolved. */
export function fieldRows(result: RunResult): FieldRow[] {
  const byEvidence = new Map(result.evidence.map((e) => [e.id, e]))
  const byIssue = new Map(result.validation.issues.map((i) => [i.id, i]))
  return Object.entries(result.field_meta)
    .map(([pointer, meta]) => ({
      pointer,
      value: getPointer(result.result, pointer),
      meta,
      evidence: meta.evidence.map((id) => byEvidence.get(id)).filter((e): e is Evidence => !!e),
      issues: meta.issues.map((id) => byIssue.get(id)).filter((i): i is Issue => !!i),
    }))
    .sort((a, b) => a.pointer.localeCompare(b.pointer, undefined, { numeric: true }))
}

export interface Box {
  evidenceId: string
  pointer: string | null
  page: number
  bbox: BBox
  text: string | null
  confidence: number | null
  producer: string
}

/** Document-region evidence with a bbox, grouped by page, with the field pointing at it. */
export function evidenceBoxes(result: RunResult): Box[] {
  const pointerOf = new Map<string, string>()
  for (const [pointer, meta] of Object.entries(result.field_meta)) for (const id of meta.evidence) pointerOf.set(id, pointer)
  const boxes: Box[] = []
  for (const e of result.evidence) {
    if (e.kind !== 'document_region' || !e.bbox) continue
    boxes.push({
      evidenceId: e.id,
      pointer: pointerOf.get(e.id) ?? null,
      page: e.page,
      bbox: e.bbox,
      text: e.text ?? null,
      confidence: e.anchor_confidence ?? e.confidence ?? null,
      producer: e.producer.name,
    })
  }
  return boxes
}

export function pagesWithEvidence(boxes: Box[]): number[] {
  return [...new Set(boxes.map((b) => b.page))].sort((a, b) => a - b)
}

/** The page size in PDF points, from the asset manifest (used to place boxes as percentages). */
export function pageSizePt(asset: Asset | undefined, page: number): { width: number; height: number } | null {
  const sizes = asset?.manifest.pdf?.page_sizes_pt
  if (!sizes || !sizes[page - 1]) return null
  const [width, height] = sizes[page - 1]
  return { width, height }
}

/** Percent-based placement of a box on its page, independent of render dpi. */
export function boxStyle(bbox: BBox, page: { width: number; height: number }): {
  left: string
  top: string
  width: string
  height: string
} {
  return {
    left: `${(bbox.x0 / page.width) * 100}%`,
    top: `${(bbox.y0 / page.height) * 100}%`,
    width: `${((bbox.x1 - bbox.x0) / page.width) * 100}%`,
    height: `${((bbox.y1 - bbox.y0) / page.height) * 100}%`,
  }
}

/** The asset an extraction came from: evidence carries asset ids, the run's inputs are assets. */
export function primaryAssetId(result: RunResult): string | null {
  for (const e of result.evidence) if (e.kind === 'document_region' || e.kind === 'image_region') return e.asset_id ?? null
  return null
}

export function assetIdsInEvidence(result: RunResult): string[] {
  const ids = new Set<string>()
  for (const e of result.evidence) if ((e.kind === 'document_region' || e.kind === 'image_region') && e.asset_id) ids.add(e.asset_id)
  return [...ids]
}
