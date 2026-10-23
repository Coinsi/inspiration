import { Input } from "./ui/input";

export type ModelInputCapabilities = {
  version: 1;
  modalities: ("text" | "image" | "audio")[];
  max_images: number;
  max_audio_clips: number;
  max_audio_seconds: number;
  max_media_bytes: number;
};
export const textInputCapabilities: ModelInputCapabilities = {
  version: 1,
  modalities: ["text"],
  max_images: 0,
  max_audio_clips: 0,
  max_audio_seconds: 0,
  max_media_bytes: 8 * 1024 * 1024,
};

export function ModelInputCapabilitiesFields({
  value,
  onChange,
}: {
  value: ModelInputCapabilities;
  onChange: (value: ModelInputCapabilities) => void;
}) {
  const image = value.modalities.includes("image");
  const audio = value.modalities.includes("audio");
  function toggle(kind: "image" | "audio", checked: boolean) {
    const modalities = checked
      ? [...value.modalities, kind]
      : value.modalities.filter((item) => item !== kind);
    onChange({
      ...value,
      modalities,
      ...(kind === "image"
        ? { max_images: checked ? 4 : 0 }
        : {
            max_audio_clips: checked ? 1 : 0,
            max_audio_seconds: checked ? 15 : 0,
          }),
    });
  }
  return (
    <div className="model-form grid gap-4">
      <p className="text-sm text-muted-foreground">
        按当前模型及服务文档选择可理解的内容。未启用时只发送文字；保存能力声明不会调用模型或产生费用。
      </p>
      <label className="model-check flex items-center gap-2">
        <input
          type="checkbox"
          checked={image}
          onChange={(event) => toggle("image", event.target.checked)}
        />
        可以理解图片与视频抽帧
      </label>
      <label className="model-check flex items-center gap-2">
        <input
          type="checkbox"
          checked={audio}
          onChange={(event) => toggle("audio", event.target.checked)}
        />
        可以理解音频片段（WAV）
      </label>
      {(image || audio) && (
        <div className="model-form-grid grid gap-3 sm:grid-cols-2">
          {image && (
            <label>
              每次最多图片数
              <Input
                type="number"
                required
                min={1}
                max={16}
                value={value.max_images}
                onChange={(event) =>
                  onChange({ ...value, max_images: Number(event.target.value) })
                }
              />
            </label>
          )}
          {audio && (
            <>
              <label>
                每次最多音频片段
                <Input
                  type="number"
                  required
                  min={1}
                  max={4}
                  value={value.max_audio_clips}
                  onChange={(event) =>
                    onChange({
                      ...value,
                      max_audio_clips: Number(event.target.value),
                    })
                  }
                />
              </label>
              <label>
                每段音频最多秒数
                <Input
                  type="number"
                  required
                  min={0.1}
                  max={120}
                  step={0.1}
                  value={value.max_audio_seconds}
                  onChange={(event) =>
                    onChange({
                      ...value,
                      max_audio_seconds: Number(event.target.value),
                    })
                  }
                />
              </label>
            </>
          )}
          <label>
            媒体总大小上限（MB）
            <Input
              type="number"
              required
              min={1}
              max={32}
              step={1}
              value={value.max_media_bytes / 1048576}
              onChange={(event) =>
                onChange({
                  ...value,
                  max_media_bytes: Number(event.target.value) * 1048576,
                })
              }
            />
          </label>
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        视频会按所选区间读取关键帧与音轨；抽帧不代表理解整段视频，文字转写也不等于听过原音频。
      </p>
    </div>
  );
}
