import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(
  new URL("../src/lib/billing.ts", import.meta.url),
  "utf8",
);
const { outputText } = ts.transpileModule(source, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ES2022,
  },
});
const {
  parseMinor,
  points,
  money,
  currencyDecimals,
  checkoutLink,
  qrImage,
  orderLabel,
} = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`
);

test("points and payment minor units remain distinct and reject silent rounding", () => {
  assert.equal(points(1200), "12");
  assert.equal(points(1255, false), "12.55");
  assert.equal(parseMinor("12.34"), 1234);
  assert.equal(parseMinor("0.01"), 1);
  for (const value of [
    "12.345",
    "1e3",
    "-1",
    "0",
    "NaN",
    "Infinity",
    "9007199254740992",
  ])
    assert.equal(parseMinor(value), null, value);
  assert.equal(currencyDecimals("JPY"), 0);
  assert.equal(parseMinor("1200", 0), 1200);
  assert.equal(parseMinor("12.1", 0), null);
  assert.match(money(1200, "jpy", false), /1,200/);
  assert.match(money(1200, "usd", false), /12\.00/);
});

test("checkout surfaces reject unsafe protocols and non-PNG image payloads", () => {
  assert.equal(
    checkoutLink("https://checkout.example.test/session/123"),
    "https://checkout.example.test/session/123",
  );
  for (const url of [
    "javascript:alert(1)",
    "data:text/html,hi",
    "http://example.test",
    "https://user:secret@example.test",
    "/relative",
  ])
    assert.equal(checkoutLink(url), null);
  assert.equal(
    qrImage("data:image/png;base64,aGVsbG8="),
    "data:image/png;base64,aGVsbG8=",
  );
  for (const url of [
    "data:image/svg+xml,<svg/>",
    "https://example.test/qr.png",
    "javascript:bad",
  ])
    assert.equal(qrImage(url), null);
});

test("paid status never claims points have arrived", () => {
  assert.match(orderLabel("paid"), /处理中/);
  assert.equal(orderLabel("completed"), "积分已到账");
  assert.notEqual(orderLabel("paid", false), orderLabel("completed", false));
});
