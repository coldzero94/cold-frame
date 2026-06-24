// Step 2 of the codegen pipeline: api.schema.json (emitted by scripts/gen_api_types.py from the
// Python wire contract) → src/api.generated.ts via json-schema-to-typescript. Run by `gen:types`.
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'
import { compile } from 'json-schema-to-typescript'

const schemaPath = fileURLToPath(new URL('../src/api.schema.json', import.meta.url))
const outPath = fileURLToPath(new URL('../src/api.generated.ts', import.meta.url))

const schema = JSON.parse(readFileSync(schemaPath, 'utf8'))
const ts = await compile(schema, 'ColdframeApiContract', {
  unreachableDefinitions: true, // emit every $def, not just those reachable from the (empty) root
  additionalProperties: false,
  bannerComment:
    '/* eslint-disable */\n// GENERATED from cold_frame/ui/contract.py — DO NOT EDIT.\n// Regenerate with `pnpm run gen:types`.',
  style: { singleQuote: true, semi: false },
})
writeFileSync(outPath, ts)
console.log('wrote src/api.generated.ts')
