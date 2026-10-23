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
  assert.equal(actionReply({ type: 'reply', text: '选择人物', $input: { name: '林舟' } }), '选择人物\nname：林舟');
  assert.throws(() => actionReply({ type: 'delete', id: 'project' }));
  assert.throws(() => actionReply({ type: 'reply', text: 'x'.repeat(8001) }));
});

test('form replies use the visible labels and preserve user values', () => {
  const form = { _type: 'Form', _action: { type: 'reply', text: '继续创作' }, children: [
    { _type: 'Input', name: 'name', label: '姓名' },
    { _type: 'Input', name: 'fear', label: '他害怕的事' },
    { _type: 'Select', name: 'occupation', label: '职业', options: [{ value: 'photographer', label: '自由摄影师' }] },
  ] };
  const tree = { _type: 'Col', children: [form, { _type: 'Form', _action: { type: 'reply', text: '另一份' }, children: [{ _type: 'Input', name: 'name', label: '其他姓名' }] }] };
  assert.equal(actionReply({ type: 'reply', text: '继续创作', $input: { name: '许知远', occupation: 'photographer', fear: '失去亲人\n也失去自我', note: '用户补充' } }, tree),
    '继续创作\n姓名：许知远\n职业：自由摄影师\n他害怕的事：失去亲人\n也失去自我\nnote：用户补充');
  assert.throws(() => actionReply({ type: 'reply', $input: { name: '文'.repeat(8001) } }, tree));
});
