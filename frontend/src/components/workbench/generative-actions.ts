import type { GenerativeUIAction } from "@assistant-ui/generative-ui/react";

export function actionReply(action: GenerativeUIAction): string {
  if (action.type !== "reply") throw new Error("不支持的操作，请通过对话继续。");
  const label = typeof action.text === "string" ? action.text : "我确认了以下选择，请继续：";
  const input = action.$input ?? action.value;
  const text = input === undefined ? label : `${label}\n${JSON.stringify(input, null, 2)}`;
  if (text.length > 8000) throw new Error("填写内容超过消息长度限制，请缩短后重试。");
  return text;
}
