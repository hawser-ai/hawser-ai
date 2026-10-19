/** JSON-pointer helpers (RFC 6901) matching hawser.structured.pointers. */

export function tokens(pointer: string): string[] {
  if (!pointer) return []
  return pointer
    .split('/')
    .slice(1)
    .map((t) => t.replace(/~1/g, '/').replace(/~0/g, '~'))
}

export function getPointer(data: unknown, pointer: string): unknown {
  let cur: unknown = data
  for (const tok of tokens(pointer)) {
    if (Array.isArray(cur)) cur = cur[Number(tok)]
    else if (cur && typeof cur === 'object') cur = (cur as Record<string, unknown>)[tok]
    else return undefined
  }
  return cur
}

export function setPointer<T>(data: T, pointer: string, value: unknown): T {
  const toks = tokens(pointer)
  if (!toks.length) return value as T
  const out = structuredClone(data ?? ({} as T)) as unknown
  let cur = out as Record<string, unknown> | unknown[]
  for (let i = 0; i < toks.length - 1; i++) {
    const tok = toks[i]
    const key: string | number = Array.isArray(cur) ? Number(tok) : tok
    const next = (cur as Record<string, unknown>)[key as string]
    if (next === undefined || next === null || typeof next !== 'object') {
      ;(cur as Record<string, unknown>)[key as string] = /^\d+$/.test(toks[i + 1]) ? [] : {}
    }
    cur = (cur as Record<string, unknown>)[key as string] as Record<string, unknown> | unknown[]
  }
  const last = toks[toks.length - 1]
  ;(cur as Record<string, unknown>)[(Array.isArray(cur) ? Number(last) : last) as string] = value
  return out as T
}

export function parentPointer(pointer: string): string {
  const i = pointer.lastIndexOf('/')
  return i <= 0 ? '' : pointer.slice(0, i)
}

export function leafName(pointer: string): string {
  const t = tokens(pointer)
  return t.length ? t[t.length - 1] : ''
}

export function isLeaf(value: unknown): boolean {
  return value === null || typeof value !== 'object'
}

/** Depth-first leaf pointers of a JSON value (arrays and objects are containers). */
export function leafPointers(data: unknown, prefix = ''): string[] {
  if (isLeaf(data)) return [prefix]
  const out: string[] = []
  if (Array.isArray(data)) data.forEach((v, i) => out.push(...leafPointers(v, `${prefix}/${i}`)))
  else for (const [k, v] of Object.entries(data as Record<string, unknown>)) out.push(...leafPointers(v, `${prefix}/${k}`))
  return out
}

/** A JSON Schema inferred from an example value (the playground's "from example" helper). */
export function schemaFromExample(value: unknown): Record<string, unknown> {
  if (value === null) return { type: 'string' }
  if (Array.isArray(value)) return { type: 'array', items: value.length ? schemaFromExample(value[0]) : { type: 'string' } }
  switch (typeof value) {
    case 'number':
      return Number.isInteger(value) ? { type: 'integer' } : { type: 'number' }
    case 'boolean':
      return { type: 'boolean' }
    case 'object': {
      const props: Record<string, unknown> = {}
      for (const [k, v] of Object.entries(value as Record<string, unknown>)) props[k] = schemaFromExample(v)
      return { type: 'object', properties: props, required: Object.keys(props), additionalProperties: false }
    }
    default:
      return { type: 'string' }
  }
}

/** Leaf pointers an object schema describes (nested objects followed, arrays stop at the array). */
export function schemaLeafPointers(schema: unknown, prefix = ''): string[] {
  if (!schema || typeof schema !== 'object') return []
  const s = schema as Record<string, unknown>
  const props = s.properties as Record<string, unknown> | undefined
  if (s.type === 'object' && props) {
    const out: string[] = []
    for (const [k, v] of Object.entries(props)) {
      const child = v as Record<string, unknown>
      if (child.type === 'object' && child.properties) out.push(...schemaLeafPointers(child, `${prefix}/${k}`))
      else out.push(`${prefix}/${k}`)
    }
    return out
  }
  return prefix ? [prefix] : []
}

export function tryParseJson(text: string): { value: unknown; error: string | null } {
  if (!text.trim()) return { value: undefined, error: null }
  try {
    return { value: JSON.parse(text), error: null }
  } catch (e) {
    return { value: undefined, error: (e as Error).message }
  }
}
