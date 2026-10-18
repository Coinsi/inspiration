import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";
const source = readFileSync(
  new URL("../src/lib/timeline-edit.ts", import.meta.url),
  "utf8",
);
const js = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { duplicateClip, splitClip, edit, undo, redo, withInstanceIds, editableContent } = await import(
  "data:text/javascript;base64," + Buffer.from(js).toString("base64")
);
const clip = {
  id: "stable-clip",
  shot_id: "shot",
  generation_id: "variant",
  in_point_ms: 1200,
  out_point_ms: 0,
  duration_ms: 3000,
};
test("split preserves media and duration with contiguous source ranges", () => {
  const clips = splitClip([clip], 0, 1100);
  assert.equal(clips[0].duration_ms + clips[1].duration_ms, 3000);
  assert.equal(clips[1].in_point_ms, 2300);
  assert.equal(clips[1].generation_id, "variant");
  assert.equal(clips[0].id, clip.id);
  assert.notEqual(clips[1].id, clip.id);
  assert.equal(clip.duration_ms, 3000);
  for (const offset of [0, 50, 3000, NaN, Infinity, 1100.5])
    assert.throws(() => splitClip([clip], 0, offset));
  assert.throws(() => splitClip(Array(100).fill(clip), 0, 1000));
});
test("undo and redo retain exact values and clear redo on a new edit", () => {
  const start = { past: [], present: [clip], future: [] };
  const changed = edit(start, splitClip([clip], 0, 1500));
  assert.deepEqual(undo(changed).present, [clip]);
  assert.deepEqual(redo(undo(changed)), changed);
  assert.equal(edit(undo(changed), []).future.length, 0);
  let history = start;
  for (let i = 0; i < 120; i++)
    history = edit(history, [{ ...clip, duration_ms: i }]);
  assert.equal(history.past.length, 100);
  assert.equal(edit(start, [clip]), start);
});

test("imported captions keep identity through reordering and restored ranges normalize", () => {
  const initial = withInstanceIds({ items: [clip], audio: [], visuals: [], subtitles: [
    { text: "A", start_ms: 0, end_ms: 500 }, { text: "B", start_ms: 500, end_ms: 1000 },
  ] });
  const moved = withInstanceIds({ ...initial, subtitles: [...initial.subtitles].reverse() });
  assert.equal(moved.subtitles[1].id, initial.subtitles[0].id);
  assert.notEqual(moved.subtitles[0].id, moved.subtitles[1].id);
  const restored = editableContent({ ...initial, items: [{ ...clip, out_point_ms: 2200 }] });
  assert.equal(restored.items[0].duration_ms, 1000);
  assert.equal(restored.items[0].in_point_ms, 1200);
  assert.equal(restored.items[0].out_point_ms, 0);
  assert.equal(restored.items[0].id, clip.id);
});

test("duplicates have independent identity and undo restores the exact original", () => {
  const original = [{...clip, transition: null}];
  const next = duplicateClip(original, 0);
  assert.equal(next.length, 2);
  assert.notEqual(next[0].id, next[1].id);
  assert.equal(next[0].generation_id, next[1].generation_id);
  assert.deepEqual(undo(edit({past:[],present:original,future:[]},next)).present, original);
});
