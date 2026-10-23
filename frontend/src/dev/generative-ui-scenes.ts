const text = (value: string) => ({ _type: "Text", value });
const caption = (value: string) => ({ _type: "Caption", value });
const badge = (value: string) => ({ _type: "Badge", value });
export const scenes: { id: string; label: string; prompt: string; intro: string; tree: Record<string, unknown> }[] = [
  { id: "visual", label: "图文建议", prompt: "这三个画面里，哪个更适合悬疑短片的开场？", intro: "我会选中间的雨夜城市。人物很小，城市很大，悬疑感可以从这份不确定里慢慢生长。", tree: {
    _type: "Col", children: [
      { _type: "Header", text: "让观众先走进雨夜，再靠近人物", size: "2xl" },
      { _type: "Image", src: "/images/studio-scenes.webp", alt: "暖色旧巷、冷色雨夜城市与日落山海的氛围比较" },
      { _type: "Markdown", value: "**中间画面的优势是空间压迫感。** 冷色的城市、地面的倒影和孤立的人物，已经能建立情绪，不需要马上解释发生了什么。\n\n- **旧巷**更适合秘密与往事，气氛温暖，但也能隐藏危险。\n- **雨夜城市**适合追踪与未知，先用远景建立孤独，再切到人物的细节。\n- **山海日落**更适合回忆或结尾，让紧张之后的情绪慢慢落下。\n\n### 开场可以这样拍\n\n先保留几秒雨声和空镜。人物走入画面后，再切到他握紧的手——让观众先好奇，再得到答案。" },
      { _type: "Button", label: "沿雨夜城市的方向细化", buttonStyle: "outline", _action: { type: "reply", text: "沿雨夜城市的方向继续，帮我细化开场的三个镜头。" } },
    ],
  } },
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
  { id: "story", label: "剧情方向", prompt: "给我两个不同的剧情方向，我想先比较一下。", intro: "## 一个雨夜，两种故事\n\n想让观众追问真相，就选《雨夜归途》；想让观众走近人物，就选《无人签收的信》。", tree: {
    _type: "Row", children: [
      { _type: "Card", title: "雨夜归途", children: [caption("悬疑 · 克制"), text("末班车开走后，林舟在旧车站收到一卷胶片。照片里的姐姐，站在明天才会拆除的房子前。"), { _type: "Markdown", value: "**越接近真相，越无法相信自己的记忆。**\n\n每洗出一张照片，他就离姐姐更近，也离记忆中的她更远。" }], confirm: { label: "展开悬疑线", _action: { type: "reply", text: "选择雨夜归途：围绕胶片与姐姐的秘密展开三幕大纲。" } } },
      { _type: "Card", title: "无人签收的信", children: [caption("剧情 · 温柔"), text("整理姐姐的旧物时，林舟发现十二封没有寄出的信。他沿着信上的地址，重新走过她生活过的城市。"), { _type: "Markdown", value: "**寻找一个人，也重新认识自己。**\n\n每个收信人都认识一个不同的她，而最后一封信，收件人是他自己。" }], confirm: { label: "展开人物线", _action: { type: "reply", text: "选择无人签收的信：围绕十二封信展开人物关系与大纲。" } } }
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
