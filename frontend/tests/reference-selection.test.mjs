import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { create, act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { File } from 'node:buffer';

const root = fileURLToPath(new URL('..', import.meta.url));
const cache = new URL('../node_modules/.cache/reference-selection/', import.meta.url);
await mkdir(cache, { recursive: true });
const output = new URL('components.mjs', cache);
await build({
  stdin: { contents: `export * from './src/components/workbench/referenceProblem'; export * from './src/components/workbench/attachmentSelection'; export * from './src/components/workbench/ConversationAttachments'; export * from './src/lib/i18n';`, resolveDir: root, loader: 'ts' },
  outfile: fileURLToPath(output), bundle: true, packages: 'external', platform: 'node', format: 'esm', jsx: 'automatic',
  alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) }, loader: { '.css': 'empty' },
  // Component tests exercise content and callbacks; real portals are covered by the browser suite.
  plugins: [{ name: 'inline-dialog', setup(builder) { builder.onLoad({ filter: /[\\/]ui[\\/]modal\.tsx$/ }, () => ({ contents: 'export function Modal({open, children}) { return open ? <div role="dialog">{children}</div> : null; }', loader: 'tsx' })); } }],
});
const { referenceProblem, referencePickState, referenceSelectionHint, preflightAttachmentFiles, ConversationAttachmentPicker, MessageAttachments, I18nProvider } = await import(output.href);
globalThis.window = globalThis;
globalThis.document = { documentElement: {} };
globalThis.localStorage = { getItem: () => null, setItem: () => {} };

const image = { id: 'image', source_type: 'media_version', title: '人物图', kind: 'image', blob_hash: 'image-hash', role: 'reference' };
const video = { id: 'video', source_type: 'generation', title: '动作视频', kind: 'video', blob_hash: 'video-hash', role: 'reference' };
const audio = { id: 'audio', source_type: 'media_version', title: '对白音频', kind: 'audio', blob_hash: 'audio-hash', role: 'reference' };
const caps = { features: ['reference', 'video_reference'], max_reference_images: 2, max_reference_videos: 1 };
const saved = (item, id = item.id) => ({ ...item, id, filename: `${id}.file`, status: 'ready', size_bytes: 100, uploaded_bytes: 100, chunk_size: 100, saved_at: '2026-10-30T00:00:00Z' });
const draft = item => ({ client_id: item.id, filename: item.filename, kind: item.kind, role: item.role, state: 'ready', attachment: item });

test('ordinary img2img and frame-only inputs follow the backend capability contract', () => {
  assert.equal(referenceProblem({ features: ['img2img'], max_reference_images: 1 }, [image]), '');
  const frames = { features: ['first_frame', 'last_frame'], max_reference_images: 2 };
  assert.equal(referenceProblem(frames, [{ ...image, role: 'first_frame' }, { ...image, role: 'last_frame' }]), '');
  assert.match(referenceProblem(frames, [image]), /未启用普通参考图/);
  assert.match(referenceProblem(caps, [{ ...video, role: 'first_frame' }]), /视频不能作为首帧/);
});

test('selection limits count local files, saved attachments and references together by kind', () => {
  const selection = { mode: 'video', capabilities: caps, references: [image, draft(saved(image, 'local'))] };
  assert.match(referencePickState(selection, image).problem, /最多支持 2 张/);
  assert.equal(referencePickState(selection, video).problem, '');
  assert.match(referencePickState({ ...selection, references: [...selection.references, draft(saved(video))] }, video).problem, /最多支持 1 个/);
  assert.match(referencePickState(selection, audio).problem, /不支持音频/);
});

test('frame-only models choose supported available roles without treating frames as ordinary references', () => {
  const selection = { mode: 'video', capabilities: { features: ['first_frame', 'last_frame'], max_reference_images: 2 }, references: [] };
  assert.deepEqual(referencePickState(selection, image), { role: 'first_frame', problem: '' });
  assert.deepEqual(referencePickState({ ...selection, references: [{ ...image, role: 'first_frame' }] }, image), { role: 'last_frame', problem: '' });
  assert.match(referencePickState({ ...selection, mode: 'image' }, image).problem, /未启用普通参考图/);
  assert.match(referencePickState({ ...selection, references: [image, image] }, image).problem, /最多支持 2 张/);
});

test('frame slots cannot be duplicated even below the model image-count limit', () => {
  const capabilities = { features: ['first_frame', 'last_frame'], max_reference_images: 4 };
  const first = { ...image, role: 'first_frame' }, last = { ...image, role: 'last_frame' };
  assert.equal(referencePickState({ mode: 'video', capabilities, references: [first] }, first).role, 'last_frame');
  assert.match(referencePickState({ mode: 'video', capabilities, references: [first, last] }, image).problem, /各只能选一张/);
  assert.match(referenceProblem(capabilities, [first, first]), /各只能选一张/);
  assert.equal(referenceProblem(capabilities, [first, last]), '');
  const ordinary = { ...capabilities, features: [...capabilities.features, 'reference'] };
  assert.equal(referencePickState({ mode: 'video', capabilities: ordinary, references: [first] }, first).role, 'reference');
  assert.equal(referenceProblem(ordinary, [first, image]), '');
});

test('local upload preflight keeps supported files and reports each rejected filename', () => {
  const files = ['voice.mp3', 'one.png', 'two.png', 'clip.mp4', 'notes.txt'].map(name => new File(['content'], name));
  const result = preflightAttachmentFiles(files, { mode: 'video', capabilities: caps, references: [image] });
  assert.deepEqual(result.accepted.map(item => item.file.name), ['one.png', 'clip.mp4']);
  assert.equal(result.errors.length, 3);
  assert.match(result.errors[0], /^voice.mp3：.*不支持音频/);
  assert.match(result.errors[1], /^two.png：.*最多支持 2 张/);
  assert.match(result.errors[2], /^notes.txt：.*有效的图片、视频或音频/);
});

test('a single local batch reserves frame slots and discussion accepts all valid media kinds', () => {
  const images = ['one.png', 'two.png', 'three.png'].map(name => new File(['content'], name));
  const frames = preflightAttachmentFiles(images, { mode: 'video', capabilities: { features: ['first_frame', 'last_frame'], max_reference_images: 4 }, references: [] });
  assert.deepEqual(frames.accepted.map(item => item.role), ['first_frame', 'last_frame']);
  assert.match(frames.errors[0], /^three.png：.*各只能选一张/);
  const files = ['image.png', 'movie.mp4', 'voice.mp3'].map(name => new File(['content'], name));
  const discussion = preflightAttachmentFiles(files, { mode: 'text', capabilitiesError: 'offline', references: [] });
  assert.deepEqual(discussion.accepted.map(item => item.kind), ['image', 'video', 'audio']);
  assert.deepEqual(discussion.errors, []);
  assert.equal(preflightAttachmentFiles(files, { mode: 'text', references: Array(7).fill(image) }).accepted.length, 1);
});

test('old incompatible selections do not block a supported new kind, and discussion stays permissive', () => {
  assert.equal(referencePickState({ mode: 'video', capabilities: caps, references: [audio] }, image).problem, '');
  assert.equal(referencePickState({ mode: 'text', references: [image], capabilitiesError: 'offline' }, audio).problem, '');
  assert.match(referencePickState({ mode: 'text', references: Array(8).fill(image) }, audio).problem, /最多添加 8 项/);
});

test('loading, error and absent capability data never claim an unsupported model', () => {
  const base = { mode: 'video', references: [] };
  for (const [extra, expected] of [[{ capabilitiesLoading: true }, /正在读取/], [{ capabilitiesError: 'offline' }, /读取失败/], [{}, /尚未取得/]]) {
    assert.match(referencePickState({ ...base, ...extra }, video).problem, expected);
    assert.match(referenceSelectionHint({ ...base, ...extra }), expected);
    assert.doesNotMatch(referenceSelectionHint({ ...base, ...extra }), /不支持/);
  }
});

async function picker(props = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false, gcTime: Infinity } } });
  client.setQueryData(['workbench-references', 'p', '', 0, true, true], { items: [image, video, audio], has_more: false });
  client.setQueryData(['saved-conversation-attachments', 'p', '', 0], { items: [saved(image), saved(video), saved(audio)], has_more: false });
  const removed = [], picked = [], added = [];
  const uploads = { items: [], remove: item => removed.push(item), addSaved: (...args) => added.push(args), ...props.uploads };
  let tree;
  const element = React.createElement(I18nProvider, null, React.createElement(QueryClientProvider, { client }, React.createElement(ConversationAttachmentPicker, { projectId: 'p', open: true, onClose() {}, mode: 'video', capabilities: caps, references: [], pickReference: item => picked.push(item), ...props, uploads })));
  await act(async () => { tree = create(element); });
  return { tree, removed, picked, added, async cleanup() { await act(async () => tree.unmount()); client.clear(); } };
}

test('library shows disabled unsupported types and aggregate limits while selected items remain removable', async () => {
  const p = await picker({ references: [image, audio], uploads: { items: [draft(saved(image, 'local'))] } });
  try {
    const imageButton = p.tree.root.findByProps({ 'aria-label': '引用 人物图' });
    assert.equal(imageButton.props.disabled, false);
    assert.equal(p.tree.root.findByProps({ 'aria-label': '引用 动作视频' }).props.disabled, false);
    const audioButton = p.tree.root.findByProps({ 'aria-label': '引用 对白音频' });
    assert.equal(audioButton.props.disabled, false);
    await act(async () => audioButton.props.onClick());
    assert.equal(p.picked[0].id, 'audio');
  } finally { await p.cleanup(); }
  const unavailable = await picker({ references: [{ ...image, id: 'existing' }], uploads: { items: [draft(saved(image, 'local'))] } });
  try {
    assert.equal(unavailable.tree.root.findByProps({ 'aria-label': '引用 人物图' }).props.disabled, true);
    assert.equal(unavailable.tree.root.findByProps({ 'aria-label': '引用 对白音频' }).props.disabled, true);
  } finally { await unavailable.cleanup(); }
});

test('saved picker shares limits and allows deselection after changing to an unsupported model', async () => {
  const p = await picker({ capabilities: { features: [], max_reference_images: 0 }, uploads: { items: [draft(saved(video))] } });
  try {
    await act(async () => p.tree.root.findAllByType('button').find(button => button.children.includes('对话附件')).props.onClick());
    assert.equal(p.tree.root.findByProps({ 'aria-label': '人物图' }).props.disabled, true);
    const selected = p.tree.root.findByProps({ 'aria-label': '动作视频' });
    assert.equal(selected.props.disabled, false);
    await act(async () => selected.props.onClick());
    assert.equal(p.removed[0].attachment.id, 'video');
  } finally { await p.cleanup(); }
});

test('saved frame-only additions use the same supported role as the library', async () => {
  const p = await picker({ capabilities: { features: ['first_frame'], max_reference_images: 1 } });
  try {
    await act(async () => p.tree.root.findByProps({ 'aria-label': '引用 人物图' }).props.onClick());
    assert.equal(p.picked[0].role, 'first_frame');
    await act(async () => p.tree.root.findAllByType('button').find(button => button.children.includes('对话附件')).props.onClick());
    await act(async () => p.tree.root.findByProps({ 'aria-label': '人物图' }).props.onClick());
    assert.equal(p.added[0][0].role, 'first_frame');
  } finally { await p.cleanup(); }
});

test('unknown capabilities disable new picks while errors offer a retry', async () => {
  let retries = 0;
  const p = await picker({ capabilities: undefined, capabilitiesError: 'offline', onRetryCapabilities: () => retries++ });
  try {
    assert.equal(p.tree.root.findByProps({ 'aria-label': '引用 人物图' }).props.disabled, true);
    await act(async () => p.tree.root.findAllByType('button').find(button => button.children.includes('重新读取')).props.onClick());
    assert.equal(retries, 1);
  } finally { await p.cleanup(); }
});

test('historical attachment previews and reuse preserve attachment and reference identities', async () => {
  const reused = [], attachment = saved(audio);
  let tree;
  await act(async () => { tree = create(React.createElement(MessageAttachments, { projectId: 'p', attachments: [attachment], references: [image], onReuse: item => reused.push(item) })); });
  try {
    await act(async () => tree.root.findByProps({ 'aria-label': '预览 对白音频' }).props.onClick());
    assert.equal(tree.root.findByType('audio').props.controls, true);
    await act(async () => tree.root.findByProps({ 'aria-label': '再次引用 对白音频' }).props.onClick());
    await act(async () => tree.root.findByProps({ 'aria-label': '再次引用 人物图' }).props.onClick());
    assert.equal(reused[0].id, attachment.id);
    assert.equal(reused[0].filename, attachment.filename);
    assert.equal(reused[0].source_type, 'attachment');
    assert.equal(reused[1], image);
  } finally { await act(async () => tree.unmount()); }
});
