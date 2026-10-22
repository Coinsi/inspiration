import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { renderToStaticMarkup } from 'react-dom/server';
import { defaultGenerativeUILibrary, renderGenerativeUI, buildPresentParameters } from '@assistant-ui/generative-ui/react';

const fixtures = JSON.parse(await readFile(new URL('../src/dev/generative-ui-fixtures.json', import.meta.url), 'utf8'));
test('all 35 official components render and match the backend contract', async () => {
  assert.deepEqual(fixtures.map(f => f._type).sort(), Object.keys(defaultGenerativeUILibrary).sort());
  const schema = JSON.parse(await readFile(new URL('../../backend/app/modules/tools/generative-ui-schema.json', import.meta.url), 'utf8'));
  assert.deepEqual(schema, buildPresentParameters(defaultGenerativeUILibrary));
  for (const fixture of fixtures) {
    assert.doesNotThrow(() => renderToStaticMarkup(renderGenerativeUI(fixture, defaultGenerativeUILibrary)));
  }
});

const cache = new URL('../node_modules/.cache/generative-tests/', import.meta.url);
await mkdir(cache, { recursive: true });
const output = new URL('message.mjs', cache);
await build({ entryPoints: [fileURLToPath(new URL('../src/components/workbench/generative-actions.ts', import.meta.url))], outfile: fileURLToPath(output), bundle: true, packages: 'external', platform: 'node', format: 'esm' });
const { actionReply } = await import(output.href);
test('interaction preserves user fields and cannot dispatch arbitrary operations', () => {
  assert.equal(actionReply({ type: 'reply', text: '选择人物', $input: { name: '林舟' } }), '选择人物\n{\n  "name": "林舟"\n}');
  assert.throws(() => actionReply({ type: 'delete', id: 'project' }));
  assert.throws(() => actionReply({ type: 'reply', text: 'x'.repeat(8001) }));
});
