import type { GenerativeUIAction } from "@assistant-ui/generative-ui/react";

type Field = { label: string; options?: { label: string; value: unknown }[] };

function fieldLabels(tree: unknown, action: GenerativeUIAction): Map<string, Field> {
  const fields = new Map<string, Field>();
  const forms: Record<string, unknown>[] = [];
  const walk = (node: unknown, visit: (value: Record<string, unknown>) => void) => {
    if (Array.isArray(node)) { node.forEach(child => walk(child, visit)); return; }
    if (!node || typeof node !== "object") return;
    const value = node as Record<string, unknown>;
    visit(value);
    if (value.children) walk(value.children, visit);
  };
  walk(tree, value => {
    const command = value._action as Record<string, unknown> | undefined;
    if (value._type === "Form" && command?.type === action.type && command?.text === action.text) forms.push(value);
  });
  // Scope labels to the submitted form when several forms share a field name.
  walk(forms.length === 1 ? forms[0] : tree, value => {
    if (typeof value.name !== "string" || typeof value.label !== "string") return;
    fields.set(value.name, {
      label: value.label,
      options: Array.isArray(value.options) ? value.options.filter((option): option is { label: string; value: unknown } =>
        !!option && typeof option === "object" && typeof option.label === "string" && "value" in option) : undefined,
    });
  });
  return fields;
}

function readableValue(value: unknown, field?: Field): string {
  const option = field?.options?.find(option => option.value === value);
  if (option) return option.label;
  if (Array.isArray(value)) return value.map(item => readableValue(item, field)).join("、");
  if (typeof value === "boolean") return value ? "是" : "否";
  if (value === null || value === undefined) return "未填写";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

export function actionReply(action: GenerativeUIAction, tree?: unknown): string {
  if (action.type !== "reply") throw new Error("不支持的操作，请通过对话继续。");
  const label = typeof action.text === "string" ? action.text : "我确认了以下选择，请继续：";
  const input = action.$input ?? action.value;
  const fields = fieldLabels(tree, action);
  const values = input && typeof input === "object" && !Array.isArray(input)
    ? Object.entries(input).map(([key, value]) => `${fields.get(key)?.label || key}：${readableValue(value, fields.get(key))}`).join("\n")
    : readableValue(input);
  const text = input === undefined ? label : `${label}\n${values}`;
  if (text.length > 8000) throw new Error("填写内容超过消息长度限制，请缩短后重试。");
  return text;
}
