// Official assistant-ui registry assets (MIT); run explicitly when updating the pinned package.
import { writeFile, mkdir } from 'node:fs/promises';
import { defaultGenerativeUILibrary, buildPresentParameters } from '@assistant-ui/generative-ui/react';

const folder = new URL('../src/components/assistant-ui/elements/', import.meta.url);
await mkdir(folder, { recursive: true });
const registry = async name => {
  const response = await fetch(`https://r.assistant-ui.com/base/${name}.json`);
  if (!response.ok) throw new Error(`${name}: ${response.status}`);
  return response.json();
};
for (const name of ['generative-ui', 'elements-mermaid-diagram', 'elements-geo-map', 'elements-surfaces']) {
  const data = await registry(name);
  for (const file of data.files ?? []) {
    const filename = file.path.split('/').at(-1);
    await writeFile(new URL(filename, folder), '// Source: https://www.assistant-ui.com — MIT\n' + file.content);
  }
}
const styles = await registry('generative-ui-style');
function css(rules, nested = false) {
  return Object.entries(rules).map(([selector, values]) => {
    if (selector.startsWith('@')) return `${selector} {\n${css(values, true)}\n}`;
    const scoped = selector.split(',').map(s => `.generative-ui ${s.trim()}`).join(', ');
    const adapt = value => String(value).replace(/var\(--(primary|border|ring)\)/g, 'hsl(var(--$1))').replace(/var\(--muted\)/g, 'hsl(var(--elevated))');
    return `${scoped} {\n${Object.entries(values).map(([k,v]) => `  ${k}: ${adapt(v)};`).join('\n')}\n}`;
  }).join('\n');
}
await writeFile(new URL('generative-ui-official.css', folder), '/* assistant-ui official registry styles, MIT; scoped to generated messages. */\n' + css(styles.css));
await writeFile(new URL('../../backend/app/modules/tools/generative-ui-schema.json', import.meta.url), JSON.stringify(buildPresentParameters(defaultGenerativeUILibrary), null, 2) + '\n');
console.log(`Exported ${Object.keys(defaultGenerativeUILibrary).length} components and present schema.`);
