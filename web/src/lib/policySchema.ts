/** Minimal JSON-Schema check: unknown properties and wrong primitive types at any depth. */
export function validate(value: Record<string, unknown>, schema: Record<string, unknown>, path = ''): string[] {
  const defs = (schema.$defs ?? {}) as Record<string, Record<string, unknown>>
  const out: string[] = []
  const walk = (v: unknown, s: Record<string, unknown>, p: string) => {
    if (typeof s.$ref === 'string') {
      const name = s.$ref.replace('#/$defs/', '')
      if (defs[name]) return walk(v, defs[name], p)
    }
    if (Array.isArray(s.anyOf)) {
      const options = s.anyOf as Record<string, unknown>[]
      if (v === null && options.some((o) => o.type === 'null')) return
      const nonNull = options.filter((o) => o.type !== 'null')
      if (nonNull.length === 1) return walk(v, nonNull[0], p)
      return
    }
    const type = s.type
    if (type === 'object' && v && typeof v === 'object' && !Array.isArray(v)) {
      const props = (s.properties ?? {}) as Record<string, Record<string, unknown>>
      for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
        if (!(k in props) && s.additionalProperties === false) out.push(`${p || '/'}: unknown property "${k}"`)
        else if (props[k]) walk(val, props[k], `${p}/${k}`)
      }
    } else if (type === 'array' && Array.isArray(v) && s.items) {
      v.forEach((x, i) => walk(x, s.items as Record<string, unknown>, `${p}/${i}`))
    } else if (typeof type === 'string' && v !== null && v !== undefined) {
      const ok =
        (type === 'string' && typeof v === 'string') ||
        (type === 'number' && typeof v === 'number') ||
        (type === 'integer' && Number.isInteger(v)) ||
        (type === 'boolean' && typeof v === 'boolean') ||
        (type === 'object' && typeof v === 'object') ||
        (type === 'array' && Array.isArray(v))
      if (!ok) out.push(`${p}: expected ${type}`)
      if (typeof v === 'number' && typeof s.minimum === 'number' && v < s.minimum) out.push(`${p}: must be ≥ ${s.minimum}`)
    }
  }
  walk(value, schema, path)
  return out
}
