# 工程数据与操作契约

状态：0.3 基础设计契约，样例包保持格式 2。字段和命令名称用于实现讨论，不是当前线上 API。与 [设计总览](README.md)、[细化契约](DETAILED_CONTRACTS.md)、[场景验收](SCENARIOS.md) 及 [最新校验记录](DESIGN_REVIEW_03.md) 配套。

## 标识与版本

| 类型 | 必需字段 | 约束 |
| --- | --- | --- |
| Work | id、project_id、title | 不强制 script_id；名称可独立更新 |
| Branch | id、work_id、head_commit、revision | 更新用比较并交换；权限不从文档内容取得 |
| Commit | id、parents、roots、requirements、decisions、author、schema_version | 不可变；只引用已发布修订 |
| ObjectRevision | id、revision、kind、schema_version、inputs、outputs、payload | 身份和修订组成唯一键；已发布后内容不可变 |
| ResourceRevision | ObjectRevision 加 blob 清单和技术描述 | 无物理 blob 的过程型内容使用文档类型，不伪装成原始文件 |
| ChangeSet | id、base_commit、reads、writes、commands、requirements、result、status | 一次跨对象原子修改，禁止直接覆盖 HEAD |
| Output | name、target、streams、profile、review_ref | 所有流固定同一根修订；各输出可有不同根 |
| Observation | target、method、source_revision、result、confidence | 不自动写入创作要求 |
| Decision | target_usage、candidate_revision、reviewed_artifact、decision、actor | 批准范围与版本明确，不是素材全局标记 |
| Artifact | input_commit、output、build_fingerprint、blob、quality_profile | 严格区分缓存与必须保留的生成源内容 |

正式系统使用 UUID 或同等稳定身份；样例用可读短标识便于审查。`revision` 示例为正整数，生产可用不可变修订 ID。内容哈希用于校验和计算缓存，不用显示名称计算身份。

对象内容与元信息分离。重命名和收藏不应改变用于像素计算的输入指纹，但创作工具若实际读取名称作为语义输入，应将相应元信息修订纳入该任务读集。

### 输出束

输出束使用一次 `target` 引用锁定合成/序列版本，`streams` 仅声明端口，不允许单独写 target。成片视频和音轨共同升级。格式 1 的单端口根无法明确表达带声音交付，所以本草案样例升级为 2；格式 1 的导入需要显式选择输出规格，不允许猜测用户要静音还是混音。当前基础校验器拒绝旧包；0.3 有夹具输出根转换探针，正式迁移器尚未实现。

```json
{
  "target": {"id": "sequence", "revision": 1},
  "streams": {
    "picture": {"port": "video", "accepts": "video"},
    "sound": {"port": "audio", "accepts": "audio"}
  },
  "profile": {
    "kind": "av", "width": 1920, "height": 1080,
    "frame_rate": {"n": "24", "d": "1"},
    "sample_rate": 48000, "channel_layout": "stereo"
  }
}
```

AV 规格要求画面和声音；静帧要求图像。未来静音、音频单独导出、多语言音轨需要各自版本化规格定义，不能在 AV 规格里静默丢掉声音。输出规格不只是文件后缀，还参与缓存、审批和执行预检。

## 规范引用

一个对象以命名输入连接其他对象的固定版本和输出端口：

```json
{
  "id": "glow",
  "revision": 1,
  "kind": "effect",
  "schema_version": 1,
  "inputs": {
    "image": {
      "target": {"id": "sword-trail", "revision": 1},
      "port": "image",
      "accepts": "video"
    }
  },
  "outputs": {"image": "video"},
  "payload": {
    "operator": "studio.glow@1",
    "parameters": {"intensity": {"mode": "constant", "value": 0.7, "unit": "ratio"}},
    "temporal_support": {"kind": "frame_local"}
  }
}
```

输入名在该对象内唯一，是可定位的稳定组件键，不能以数组位置代替。`accepts` 是导出时的类型声明，服务端必须与受信任的类型适配器核对，不能信任客户端自己宣称的类型。实际执行还需检查尺寸、时间、Alpha、色彩和声道等细化约束。

其他内容引用同样进入规范输入或类型声明的引用字段；不能把 ID 藏在自由文本里作为执行依赖。来源、语义与审计关系另存带类型的关系记录，不混入渲染输入。反向依赖索引由适配器抽取并可重建。

`latest` 只允许用于发现候选，不能进入发布的内容引用。根到叶的闭包可同时包含某素材的多个修订：同一素材的两次使用不必采用相同版本。

## 文档最小载荷

这些是每类文档需要支持的最低语义。基础样例只为小集合提供可执行检查，0.3 补充细化契约及独立状态模型；完整参数 schema 和实际执行器按能力模块实现。

| 类型 | 载荷 |
| --- | --- |
| resource | blobs、media_descriptor、available_ports、provenance |
| instance | 命名 source 输入、placement、source_mapping、bounds_policy、overrides、sync_group |
| sequence | dimensions、frame_rate、audio_format、color、duration、tracks、markers、transitions |
| composition | dimensions、duration、frame_rate、color、layers、public_parameters、root_ports |
| image_document | dimensions、color、layers、root_port；没有虚构的影片时长 |
| audio_document | sample_rate、channel_layout、duration、tracks、buses、root_port |
| effect | operator、parameters、temporal_support、state_policy；输入输出由实现清单定义 |
| narrative | stable elements、事件关系、身份引用、约束；不作为渲染执行图 |
| requirement | target、expected_change、preserve、references_with_roles、acceptance、scope |
| analysis | source 输入、analyzer_version、range、coordinate_space、confidence、observations |

`layers` 和 `tracks` 中的组件有稳定 ID，顺序独立保存；轨道重排和片段重排也属于需要检测冲突的修改。合成实例的源端口可以来自原始资源、图像文档或子合成。

`track.overlap_policy` 在样例中为 reject、stack 或 mix；stack 仅用于视频并依照 items 顺序由后覆盖在前，mix 仅用于音频并按混音规则处理。明确转场属于后续独立操作，不通过偷偷允许重叠代替。缺少规则或同一轨道 ID 重复时不能作为可执行序列发布。

图片持续显示需要显式的 hold 适配器；合成中的文字动画需要文字布局和动画执行器；有理数时间能够保存，不意味着任何外部模型都能接受任意精度。

## 时间值和映射

```json
{
  "placement": {
    "start": {"n": "12", "d": "1"},
    "duration": {"n": "4", "d": "1"}
  },
  "source_mapping": {
    "mode": "linear",
    "source_start": {"n": "2", "d": "1"},
    "rate": {"n": "1", "d": "2"}
  },
  "bounds_policy": "error"
}
```

以上实例在父序列 12 秒开始播放 4 秒，源内容从 2 秒开始，以半速使用 2 秒源范围。`source_time = source_start + local_time × rate`。所有分母为正整数，规范序列化约分；起止范围左闭右开，负的 rate 表示倒放，0 表示停帧。倒放起点如何选取有效采样帧，由解码适配器按时间索引解析，不允许读取不存在的 duration 端点帧。

分段映射显式保存每段的局部范围和源映射；不得有未说明的空隙或重叠。不连续跳转需单独标记。对于音频，rate 为 0 等没有有用默认含义的情况由音频适配器拒绝或要求明确策略。

超出父合成可采用明确裁剪策略；样例使用严格范围并拒绝越界。镜头素材不足以覆盖转场余量时返回 `INSUFFICIENT_HANDLES`，不隐式拉伸。帧采样、音频边界与转场数学需要执行器级验收，样例只检验有理数和常速范围。

## 参数和目标

参数值采用以下互斥模式之一：

- constant：value 与 unit。
- curve：稳定关键帧 ID、时间、值、插值和切线。
- binding：参数源或跟踪源、映射方法与空间；禁止零延迟环。

字段没有提供不能被理解为删除。显式 reset 才恢复定义版本中的默认值。默认参数、实例覆盖和曲线冲突由适配器拒绝，不能凭字段顺序决定优先级。

目标地址包含 `output`、`instance_path`、`object_id`、可选 `component_id`、`parameter_path`、时间范围和空间选择资源。引用路径由稳定输入键组成。同一素材的两处使用会得到不同地址。

```json
{
  "output": "main",
  "instance_path": ["fight", "sword_fx", "glow"],
  "object_id": "glow",
  "parameter_path": "/parameters/intensity",
  "scope": "usage_only"
}
```

空间选择保存坐标所属源修订、方向、尺寸、归一化或像素单位，动态选择保存跟踪引用；重绘后旧遮罩不自动视作准确。用户说“这个”时，客户端提供当前选择和播放位置；模型仍有歧义时不能擅自扩大作用范围。

### 锚点和参数校验

锚点分 absolute、local 和 source 三类，分别针对序列绝对时间、实例局部时间和源内容事件时间。锚点保存稳定实例目标与被识别源内容的修订。纯移动实例可重新求值位置；替换源内容需要复核旧观察；目标删除返回缺失，不自动绑定旁边一个片段。

常速映射的 source 锚点反算为 `(source_event - source_start) / rate + placement_start`。停帧时源点对应一段时间，无法唯一求逆；重复播放与分段映射也可能得到多个解，必须选择具体 occurrence 或返回需决定。不能默认取第一个解。

参数仅接受定义中的名字、模式和单位；禁止 NaN、Infinity、布尔数冒充数值、范围越界或同时提交常量与曲线。关键帧 ID 唯一，时间递增；可以在文档结束边界放曲线控制点，但媒体播放区间仍为左闭右开。歌词 cue 的起止在合成范围内、ID 唯一、正文非空。允许的字幕重叠需由布局/声部规则定义，不能把所有字幕重叠一律当错误。

## 变更接口

| 操作 | 输入 | 输出 |
| --- | --- | --- |
| read | commit、target、fields、分页游标 | 内容及实际读取版本、能力与权限摘要 |
| describe_operations | target、调用者身份 | 当前可用命令与字段 schema |
| propose | base_commit、目标、领域命令、幂等键 | ChangeSet、读写前提、影响摘要和候选分支 |
| validate | ChangeSet、目标分支、执行环境 | 错误、冲突、需复核项、渲染预检 |
| preview | 候选提交、输出、质量规格 | Run；完成后 Artifact |
| commit | ChangeSet、审批证明或直接操作授权、幂等键 | 新 Commit 或明确冲突 |
| adopt | 使用位置、候选修订、读取前提 | 新采用决定与工程提交 |
| undo | 原变更、当前分支 | 反向变更候选，可能冲突 |
| restore | 旧提交、当前分支 | 新提交指向旧内容，不抹除后续历史 |

领域命令例如 `clip.trim`、`clip.ripple_move`、`audio.set_gain`、`effect.set_parameter`、`subtitle.retime`、`usage.replace_source`。它们是内部操作，不代表主 Agent 默认看到这些所有命令。

通用 JSON Patch 只能作为受约束的传输形式，不能直接写任意库字段。适配器校验参数、资源归属、保护范围和对象不变量，并补足读取依赖。

### 提交算法

1. 依据 base_commit 解析选定实例路径，建立读集；包括源版本、受影响轨道布局、保护要求及相关约束。读集是服务端观察到的读取，不允许调用者随意删减。
2. 构造修改副本，生成新叶修订和所选父引用路径的新修订。无关路径继续引用原版本。
3. 校验结构、依赖闭包、输入输出类型、时间、参数、引用权限和扩展兼容。存在渲染环境缺失可允许保存，但不得返回可渲染。
4. 提交时重新检查目标分支及全部前提。只发生无关修改时，可以把领域命令重放到新 HEAD，再完整校验。不能直接提交基于旧根构造的整份快照。
5. 进入短事务，比较分支指针、写不可变修订和新提交、更新采用指针、写幂等回执与 outbox。指针再次变化则有限重试或返回冲突。
6. 事务后派发 outbox 任务。外部媒体发布在此之前完成；提交失败留下的未引用 blob 由延迟回收处理，不伪称跨对象存储和数据库有单一事务。

审批绑定变更指纹、实际引用和保护要求。自动重放若改变用户审核的效果，返回重新复核。多个工作者修改不同字段但依赖同一个长度或标记时会产生语义冲突；不使用“不同字段必然合并”的规则。

读集使用不会复用的修订令牌，即使值经历 A → B → A，也拒绝旧 A 的直接提交。字段内容哈希可用于确定性计算缓存，但不足以证明用户意图与采用状态未改变。并发序号由持久化服务分配；样例里的 max(revision)+1 仅用于单进程演示，不能直接用于多工作者。

### 小型并发例子

Agent A 读取对白长度为 4 秒并制作字幕。Agent B 将对白替换为 5 秒。即使 A 只写字幕、B 只写声音，A 提交时对白长度前提不符，必须重新对齐或保留为候选。

另一个 Agent 修改封面标题，且没有读取上述对白或字幕，可以重放到新 HEAD；提交后的根集合必须同时保留新对白与新封面。不能因为无冲突就用旧根覆盖 B 的修改。

## 扩展执行契约

每个操作实现登记 `type_id`、`schema_version`、`operator_version`、参数定义、端口定义、依赖提取器、约束校验器、时间影响模型、迁移器、执行环境要求及预览/正式渲染支持。

时间影响模型至少包括 frame_local、window、whole_input、stateful 和 unknown。window 指明前后所需范围；stateful 声明初始化、检查点与随机性；unknown 不允许乐观假设局部失效。

必须区分“计算输出所需的输入窗口”与“输入变化导致的输出失效窗口”。若输出 t 读取输入 `[t-before, t+after]`，输入 `[a,b)` 改变后保守失效范围为 `[a-after,b+before)`，再裁剪到输出区间。拖尾读取过去帧，影响向后续输出扩展；不能把 before/after 原样复制到失效范围。跨变速与嵌套需要继续映射，复杂未知情况整段失效。

未知对象 kind、未知 schema_version、缺少必需实现和未知 operator 均需被能力预检识别。未知载荷保持不透明，不用旧类型语义解释其中同名字段；规范 envelope 的引用和端口仍需可读，才能检查依赖。为输出 A 预检只沿 A 的闭包检查执行能力，另一个输出 B 的未知算子不应自动阻止 A；工程包本身存在结构损坏时仍需诊断。

已知实例引用未知文档时，结构检查可以验证规范端口和引用存在，但不能读取其未知载荷猜测时长。相关时间/空间检查标记为未完成，输出预检因能力缺失阻止执行。只读保留不等于这些约束已经通过。

固定版本的旧内容和旧缓存不会因新候选产生而失效。反向依赖查询给出“如果升级此引用，哪些位置需要复核或重算”；只有实际采用新引用路径时才构建新的计算键。旧提交的缓存仍对旧提交有效，缓存清理属于独立的保留策略。

执行计划冻结实际参数、资源修订、算子版本、时间与空间映射、渲染规格和环境指纹。编译器负责选择实现，Agent 不能通过改一个 operator 字符串获得额外系统权限。

预览结果附带降级清单。正式渲染若存在不支持节点、未解析资源或必需依赖缺失，必须在昂贵任务提交前报错。对实际生成质量的验证发生在结果阶段，不能由结构校验替代。

## 保存和采用状态

采用状态是工作分支里一个使用位置的决定；候选是不可变内容及提案。任务状态、资源上传状态和审批状态分别建模。

候选变更状态可为 draft、validated、awaiting_review、accepted、rejected、superseded 或 conflicted。已接受的变更内容不再改变；进一步修改创建新的变更集。运行结束可以创建候选，即使原分支已变化；自动采用必须重新进行前提验证。

资源发布状态 uploading → verifying → published 或 failed。处理进度和预览生成属于相关任务，不能修改已发布原始内容。技术探测或观察新增使用附属记录，不偷偷更换原始 blob。

## 失败行为

| 错误 | 行为 |
| --- | --- |
| MISSING_REFERENCE | 保留缺失占位，阻止需要该内容的执行 |
| PORT_MISMATCH | 禁止连接；要求显式转换 |
| DEPENDENCY_CYCLE | 拒绝发布可执行结构；返回环路径 |
| STALE_READ | 保留候选，重新读取或重放，不覆盖 HEAD |
| CONSTRAINT_VIOLATION | 返回违反的时间、轨道或保护规则 |
| UNSUPPORTED_CAPABILITY | 保存未知数据或只读打开；相关执行禁用 |
| APPROVAL_STALE | 内容或前提变化后重新复核 |
| ACCESS_REVOKED | 停止相关访问，既有缓存不构成绕过权限的理由 |
| PROVIDER_OUTCOME_UNKNOWN | 先查询对账，禁止无条件重复收费请求 |
| RESOURCE_LIMIT | 给出范围与可降级方案，不写半份工程 |

这些错误需要稳定机器码与面向用户的说明。界面默认展示受影响内容和可采取的下一步，不把类型 ID、哈希或实现参数铺给用户。

## 样例与校验边界

`example.json` 表达短片镜头、剑光合成、独立对白、动态歌词和两次复用。使用描述性的虚拟 blob 名称，不能直接渲染。`verify_design.py` 检验样例内的固定引用、端口、闭包、无环、常速时间范围，并通过独立反例检查失效传播和读取前提。

`verify_contract_edges.py` 增补可信端口声明、输出束、参数与歌词边界、锚点、时间失效和版本令牌的反例探针。`verify_lifecycle_design.py` 补充六组细化契约的选定反例，三组共 105 项检查，统一运行命令和未覆盖范围见[最新校验记录](DESIGN_REVIEW_03.md)。

它们不是完整 schema 实现，不实现数据库事务、图像合成、权限认证、计费、内容分析、所有文档类型或所有参数约束。未知扩展的测试仅保证样例校验器能保留数据并识别类型/算子缺失，不能替代真实旧客户端兼容测试。信任注册表是测试夹具，不表示已经存在对应特效引擎。
