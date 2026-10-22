const text = (value: string) => ({ _type: "Text", value });
const caption = (value: string) => ({ _type: "Caption", value });
const badge = (value: string) => ({ _type: "Badge", value });
export const scenes: { id: string; label: string; prompt: string; intro: string; tree: Record<string, unknown> }[] = [
  { id: "character", label: "人物设定", prompt: "帮我完善《雨夜归途》的主角，让他的动机更有说服力。", intro: "让他想要寻找真相，却又害怕真相。你可以调整下面的设定。", tree: {
    _type: "Card", title: "林舟 · 一个不敢回头的人", children: [
      { _type: "Row", children: [badge("悬疑短片"), badge("主角")] },
      { _type: "Form", _action: { type: "reply", text: "请按以下人物设定继续完善故事：" }, children: [
        { _type: "Row", children: [
          { _type: "Input", label: "姓名", name: "name", defaultValue: "林舟" },
          { _type: "Select", label: "职业", name: "occupation", defaultValue: "photographer", options: [{ label: "自由摄影师", value: "photographer" }, { label: "调查记者", value: "journalist" }] }
        ] },
        { _type: "Input", label: "他想要什么", name: "desire", defaultValue: "找到失踪的姐姐，弄清那卷胶片的来历。" },
        { _type: "Input", label: "他真正害怕的事", name: "fear", multiline: true, defaultValue: "姐姐失踪前曾向他求助，他却没有接听电话。他害怕找到的不是姐姐，而是自己无法弥补的过错。" },
        { _type: "Button", label: "用这个人物继续", submit: true }
      ] }
    ]
  } },
  { id: "story", label: "剧情方向", prompt: "给我两个不同的剧情方向，我想先比较一下。", intro: "同一个雨夜，可以从追寻真相出发，也可以从一封迟到的来信出发。", tree: {
    _type: "Row", children: [
      { _type: "Card", title: "雨夜归途", children: [caption("01 / 悬疑 · 克制"), text("末班车开走后，林舟在旧车站收到一卷胶片。照片里的姐姐，站在明天才会拆除的房子前。"), { _type: "Divider" }, caption("故事张力"), text("他每洗出一张照片，就离真相更近，也离记忆中的姐姐更远。")], confirm: { label: "沿这个方向展开", _action: { type: "reply", text: "选择雨夜归途：围绕胶片与姐姐的秘密展开三幕大纲。" } } },
      { _type: "Card", title: "无人签收的信", children: [caption("02 / 剧情 · 温柔"), text("整理姐姐的旧物时，林舟发现十二封没有寄出的信。他沿着信上的地址，重新走过她生活过的城市。"), { _type: "Divider" }, caption("故事张力"), text("每个收信人都认识一个不同的她，而最后一封信，收件人是他自己。")], confirm: { label: "沿这个方向展开", _action: { type: "reply", text: "选择无人签收的信：围绕十二封信展开人物关系与大纲。" } } }
    ]
  } },
  { id: "shots", label: "分镜编排", prompt: "把雨夜车站的开场整理成分镜，节奏舒缓一点。", intro: "先让观众进入车站，再靠近人物。四个镜头，用环境声串联。", tree: {
    _type: "Card", title: "开场 · 雨中的旧车站", children: [
      caption("4 个镜头 / 约 20 秒 / 外景 · 夜"),
      { _type: "Table", columns: [{ label: "镜号" }, { label: "景别 / 运镜" }, { label: "时长" }, { label: "画面与声音" }], rows: [
        ["01", "远景 · 固定", "6 秒", "空荡的站台，灯光映在积水中。雨声持续。"],
        ["02", "中景 · 跟随", "5 秒", "林舟走入画面，湿透的外套贴着肩膀。脚步声靠近。"],
        ["03", "特写 · 缓推", "4 秒", "一只手从信封里抽出胶片。纸张摩擦声。"],
        ["04", "近景 · 固定", "5 秒", "他抬头望向站台尽头。广播突然响起。"]
      ] },
      { _type: "Button", label: "继续细化镜头", _action: { type: "reply", text: "采用这四个开场分镜，请继续细化构图与光线。" } }
    ]
  } }
];
