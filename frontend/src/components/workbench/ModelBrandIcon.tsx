import "./model-brand-icon.css";
import volcengine from "@/assets/model-brands/volcengine-color.svg";
import minimax from "@/assets/model-brands/minimax-color.svg";
import qwen from "@/assets/model-brands/qwen-color.svg";
import openai from "@/assets/model-brands/openai.svg";
import claude from "@/assets/model-brands/claude-color.svg";
import gemini from "@/assets/model-brands/gemini-color.svg";
import deepseek from "@/assets/model-brands/deepseek-color.svg";

const marks: Record<string, string> = {
  火山引擎: volcengine,
  MiniMax: minimax,
  Wan: qwen,
  OpenAI: openai,
  Anthropic: claude,
  Google: gemini,
  DeepSeek: deepseek,
};

/** Original brand colors; unknown connections use initials instead of a false brand. */
export function ModelBrandIcon({
  brand,
  size = 22,
}: {
  brand: string;
  size?: number;
}) {
  const src = marks[brand];
  if (brand === "OpenAI")
    return (
      <span
        aria-hidden="true"
        className="model-brand-logo model-brand-mono"
        style={{
          width: size,
          height: size,
          maskImage: `url("${src}")`,
          WebkitMaskImage: `url("${src}")`,
        }}
      />
    );
  if (src)
    return (
      <img
        aria-hidden="true"
        alt=""
        className="model-brand-logo"
        src={src}
        width={size}
        height={size}
        draggable={false}
      />
    );
  const initials =
    brand === "开发测试"
      ? "T"
      : brand === "选择模型"
        ? "AI"
        : Array.from(brand.trim()).slice(0, 2).join("").toUpperCase();
  return (
    <span
      aria-hidden="true"
      className="model-brand-initials"
      style={{ width: size, height: size, fontSize: Math.max(9, size * 0.42) }}
    >
      {initials || "AI"}
    </span>
  );
}
