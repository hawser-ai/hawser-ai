import { json } from '@codemirror/lang-json'
import CodeMirror from '@uiw/react-codemirror'
import { useState } from 'react'
import { schemaFromExample, tryParseJson } from '@/lib/json'

interface Props {
  value: string
  onChange: (text: string) => void
  height?: string
  /** Shows the "from example JSON" helper that infers a schema from a sample object. */
  withExampleHelper?: boolean
  label?: string
}

/** A JSON editor with parse feedback. `error` is surfaced below the editor, never silently. */
export function SchemaEditor({ value, onChange, height = '260px', withExampleHelper, label = 'Output schema (JSON Schema)' }: Props) {
  const [example, setExample] = useState<string | null>(null)
  const parsed = tryParseJson(value)
  const exampleParsed = example === null ? null : tryParseJson(example)
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className="label mb-0">{label}</span>
        {withExampleHelper && (
          <button type="button" className="btn-ghost text-xs" onClick={() => setExample(example === null ? '' : null)}>
            {example === null ? 'from example JSON…' : 'close helper'}
          </button>
        )}
      </div>
      {example !== null && (
        <div className="mb-2 rounded border border-ink-200 bg-ink-50 p-2">
          <div className="mb-1 text-xs text-ink-600">Paste an example of the JSON you want back; a schema is inferred (strings, numbers, booleans, arrays, objects, all required).</div>
          <CodeMirror value={example} height="120px" extensions={[json()]} onChange={setExample} aria-label="example json" />
          <div className="mt-1 flex items-center gap-2">
            <button
              type="button"
              className="btn-primary text-xs"
              disabled={!exampleParsed || !!exampleParsed.error || exampleParsed.value === undefined}
              onClick={() => {
                if (exampleParsed?.value !== undefined) onChange(JSON.stringify(schemaFromExample(exampleParsed.value), null, 2))
                setExample(null)
              }}
            >
              Use inferred schema
            </button>
            {exampleParsed?.error && <span className="text-xs text-red-700">{exampleParsed.error}</span>}
          </div>
        </div>
      )}
      <div className="overflow-hidden rounded-md border border-ink-300" data-testid="schema-editor">
        <CodeMirror value={value} height={height} extensions={[json()]} onChange={onChange} basicSetup={{ lineNumbers: true, foldGutter: true }} />
      </div>
      {parsed.error ? (
        <div className="mt-1 text-xs text-red-700" role="alert">
          invalid JSON: {parsed.error}
        </div>
      ) : parsed.value !== undefined && (typeof parsed.value !== 'object' || parsed.value === null) ? (
        <div className="mt-1 text-xs text-red-700" role="alert">
          a schema must be a JSON object
        </div>
      ) : null}
    </div>
  )
}
