import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const { outputFiles } = await build({
  stdin: {
    contents: 'export * from "./assetChoices"; export * from "./shotLabels";',
    resolveDir: fileURLToPath(new URL("../src/lib/", import.meta.url)),
  },
  bundle: true,
  format: "esm",
  write: false,
});
const { assetChoices, shotChoices } = await import(
  "data:text/javascript;base64," +
    Buffer.from(outputFiles[0].text).toString("base64")
);

test("same-name assets keep their identity across sorting and legacy code search", () => {
  const assets = [
    { id: "b", code: "LOC-0013", name: "营房", summary: "" },
    { id: "a", code: "LOC-0012", name: "营房", summary: "" },
    { id: "c", code: "LOC-0014", name: "营房", tags: ["西坡", "雪夜"] },
  ];
  const choices = assetChoices(assets);
  assert.deepEqual(
    choices.map((a) => a.selectedLabel),
    ["营房（同名素材 2）", "营房（同名素材 1）", "营房 · 西坡 · 雪夜"],
  );
  assert.deepEqual(assetChoices([...assets].reverse()).reverse(), choices);
  assert.deepEqual(
    choices.filter((a) => a.keywords.includes("LOC-0013")).map((a) => a.value),
    ["b"],
  );
});

test("same-name shots show their scene and ordinal while keeping internal codes searchable", () => {
  const shots = [
    {
      id: "first",
      title: "回望",
      ordinal: 2,
      scene_title: "旧街",
      code: "SC-0001-SH003",
    },
    {
      id: "next",
      title: "回望",
      ordinal: 1,
      scene_title: "营区",
      code: "SC-0002-SH001",
    },
    {
      id: "untitled",
      title: null,
      ordinal: 4,
      scene_title: "营区",
      code: "SC-0002-SH007",
    },
  ];
  const choices = shotChoices(shots);
  assert.deepEqual(
    choices.map((s) => s.selectedLabel),
    ["旧街 · 第 2 镜 · 回望", "营区 · 第 1 镜 · 回望", "营区 · 第 4 镜"],
  );
  assert.equal(choices[2].label, "镜头 4");
  for (const [i, choice] of choices.entries()) {
    assert.doesNotMatch(
      [choice.label, choice.description, choice.selectedLabel].join(" "),
      /SC-/,
    );
    assert.ok(choice.keywords.includes(shots[i].code));
  }
});
