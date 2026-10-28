# Harness 媒体处理与转写接入

## 对话附件

0055 迁移增加独立的 `conversation_attachment`。图片、视频、音频可在输入框选择、粘贴或拖入，先保存为私有草稿；消息与附件来源绑定在同一事务提交。发送后自动出现在当前用户的素材库“对话附件”来源及附件选择器中，不需要索引服务，不伪装成 `LibraryMedia`，也不自动选用为作品定稿。复用保留最初来源，文件存储按 CAS 摘要去重；名称、说明、标签可以修改，原始文件名和字节保持不变。

`Send.attachments` 接受附件 ID 和用途；文字对话的 `Send.references` 还可引用既有素材版本或生成结果。生成模式把附件合并进本次 `media.references`，固定 ID、摘要、用途并重新验证权限。发送失败回滚归集状态；重试保留原输入。状态不明的付费提交禁止重发，已有可恢复上游回执的旧对话任务继续查询原任务。

本轮图片会实际解码为模型输入；视频默认提供最多两张带时间位置的抽样帧，明确未覆盖完整运动和音轨；声明支持音频的模型最多接收十五秒采样。不支持音频的模型只收到未听取提示和可用的既有转写。独立附件尚未接入仅接受 `MediaVersion` 的转写队列，不能将这些提示宣称成已完成转写。历史消息只保留固定引用，按需通过 `media.inspect` 读取；媒体字节不持久化到任务上下文中。供应商的参考类型、输入上限和传输协议限制仍然适用。

“移入回收站”仅隐藏已保存附件，保留对话和固定引用。未发送草稿默认三十天未活动后回收，配置为 `ATTACHMENT_DRAFT_TTL_DAYS`；轻量清理由 `ATTACHMENT_CLEANUP_ENABLED` 控制，不依赖索引工作进程。清理仅解除草稿引用与暂存文件，不直接删除共享 CAS 文件。

`test_conversation_attachments`、`test_attachment_conversation_flow` 验证实际隔离数据库中的上传、消息、归集、复用及重试；`test_attachment_model_context` 使用本地真实图片/音视频解码检查模型请求；`test_attachment_generation_boundary` 运行到视频供应商请求组包和参考链接读取，外部请求与回执使用协议模拟器。它们不证明真实供应商生成质量。`test_attachment_migration` 验证 0054 到 0055 的升级、降级和再次升级。

## 本地素材处理

`media.process` 是统一的本地处理入口，返回真实 `GenerationJob.job_id`，不会调用收费模型。

- `image.adjust`：规范化裁剪、90 度旋转、缩放、亮度、对比度。
- `video.frames`：按指定毫秒抽取 1–12 个不同帧。
- `video.trim`：在已有视频范围内裁剪并重新编码。
- `video.extract_audio`：提取指定时间范围的现有音轨。

每次输入必须指定作品、素材版本或生成记录 ID、固定原文件 SHA256。模型不能提供 request_key；系统从持久执行尝试注入。相同请求和参数复用原 job，参数变化会拒绝；候选不覆盖原文件，也不自动采用。

源文件最多 256 MB，音视频最多 30 分钟。图片输出最多 4000 万像素、单边 8192 像素。实际解码器还会验证文件、流、时间范围及输出大小。资源预留先于 dispatch，失败或未知消费不自动释放。处理完成后仍需进入既有完整技术校验阶段。

`engineering.delivery` 的持久 outbox 仅允许本地、明确 `harness_origin.tool=media.process` 且属于四项操作白名单的任务自动恢复。同一 job 的租约会阻挡迟到工作进程发布；不会重新提交收费模型。

## 独立转写队列

`media.transcribe` 返回 `async_kind=transcription` 和 `transcription_id`，不返回伪造的 GenerationJob ID。`media.transcription_status` 支持固定修订和有界分页读取。

自动转写只有在以下配置均满足时可用：

```dotenv
TRANSCRIBER_URL=http://127.0.0.1:8012
TRANSCRIPTION_WORKER_ENABLED=true
HARNESS_TRANSCRIPTION_COST_MODE=self_hosted
```

`HARNESS_TRANSCRIPTION_COST_MODE` 默认 `unknown`。只有管理员确认该地址是自托管、没有按调用向第三方付费的识别服务后，才能设置为 `self_hosted`。这不表示机器、网络或存储没有成本。不会根据 URL 或模型名称猜测免费。示例配置不会修改用户现有 `.env`。

Harness 先在同一事务保存转写意图、系统请求标识和资源预留，再释放制作/素材行锁，由原有识别工作进程检查服务与固定模型并执行。旧转写 HTTP API 默认行为保持兼容。0052 迁移新增执行快照，冻结原文件哈希、大小、时长、识别服务地址摘要及费用策略。工作进程在每段前后重验快照与当前权限，真实源文件还会核对 SHA256；配置或源变化不会自动切换。处理按 30 秒保存检查点，中断后使用同一任务继续未完成部分。取消时丢弃尚未提交的迟到片段，已有检查点保留。

集成函数：

- `transcription_tools.register_tools()`：注册提交、查询及只读计划预检。
- `transcription_wait.poll_waiting(db, run)`：在调用者的短 run 锁内更新 `waiting_transcription` 收据；不会调用识别、提交新任务或恢复暂停。
- `transcription_wait.recover_uncertain(db, run)`：只从已持久的相同 attempt 请求记录恢复，不查到意图就不会猜测重投。
- `transcription_wait.cancel_owned(db, run, task_ids=None)`：取消本 run 指定步骤拥有的转写意图；局部指导不会取消其他步骤的转写。

转写任务的 `expected_output` 应为 `observation`，它不是音视频 `artifact` 或工程 `candidate`。字幕默认是待人工核对的草稿，不会自动发布到素材检索，也不会修改时间线。句段字幕时间不等于逐字对齐。动态歌词需要可信逐字时间或用户校对后，通过工程字幕/叠加操作制作；不能按字符数猜出“精确”唱词时间。

## 明确未接通的能力

`media.capabilities` 返回具体支持范围、当前配置及原因。现有领域的蒙版语义重绘依赖支持 inpaint 的模型，并非图片透明度合成；它尚未被本地 process 工具包装为可自动计费调用。视频生成式延长、配音生成、音乐/音效生成、强制逐字对齐没有本工具可调用的实现。不能把裁剪、复用已有音轨或填入 JSON 宣称成这些能力。

## 验收范围

`test_harness_process_tools.py` 使用真实 PNG、FFmpeg 视频和音轨，检查固定源、像素、范围、完整技术解码、资源限制、权限、幂等及 outbox 恢复。

`test_harness_transcription_tools.py` 使用真实 WAV、FFmpeg 解码、数据库和检查点；识别端使用明确的协议模拟器。它验证执行链和恢复机制，不验证识别准确率、说话人识别或歌词逐字质量。

## 任务执行与权限变化

`job_authorization.validate_job` 在首次上游调用、每次接收、CAS 写入和最终发布前，重新读取账号、项目、成员角色、制作任务归属与固定来源。不会使用旧 Context 的角色，也不会通过工程预览悄悄扩大权限。

- 首次提交必须仍在授权计划中，费用/资源预留与 job 一致。未提交的取消、取代或失败任务不能调用生成服务。
- 上游已接受的付费任务，即使原计划随后取消、局部重做或其他步骤失败，也可以接收为未选用的不可变候选；不会复活旧步骤、自动采用或写入当前工程。
- 账号/成员权限撤销、项目删除、固定素材被移除或摘要改变，会阻止后续读取、CAS 和发布。已保存的上游句柄和费用预留保留，不能假定未扣费。
- 显式重新接收使用 `media.resume_receipt` 的固定恢复记录，复用原上游句柄，不重新生成。

`test_harness_job_authorization.py` 覆盖首次提交、已经接受、存储及发布阶段的上述区别，包括仍关联作品的生成请求。`test_harness_transcription_runtime.py` 通过真正的 `runtime.execute` 和 `service.control` 校验等待、暂停、恢复、取消及下一步唤醒。

## 远端生成投递与工作进程失联

新 Harness 的 `media.generate` 复用 `EngineeringDelivery` 持久投递表与既有后台调度器，不需要额外迁移。任务、固定输入、费用预留和投递意图同事务保存。消息发送成功不等于工作进程已接手：尚未领取的 pending 任务会重投同一 job，工作进程在任务行锁下条件领取并获得独立租约，重复消息不会重复调用供应商。

本地工程渲染和远端生成的失联策略不同。本地可从固定输入重做；远端一旦领取，租约过期后只能转入失败核对，绝不会自动重新提交。已保存、可恢复的上游句柄通过 `media.resume_receipt` 重新查询和下载同一任务；未取得句柄、提交状态不明或协议不支持恢复时，步骤明确标记 uncertain，保留费用预留，关闭普通重试。同步返回内容、没有可查询上游句柄的协议无法在进程丢失后凭空恢复结果。

Routed、Personal 及直接适配器均受执行租约约束。旧工作进程不能覆盖新的路由收据、保存成果或重复计费；丢失租约也不会向上游发出取消，因为新的接收进程可能正在使用同一任务。用户明确取消远端任务与工作进程丢失租约是不同事件。已经受理的任务仍按上述权限规则保存未选候选，工程采用保持独立。

`test_harness_remote_delivery.py` 覆盖事务回滚、消息丢失、并发重复投递、未知提交与预算保留。`test_harness_remote_recovery.py` 覆盖两类路由的固定句柄恢复、旧轮询迟到、权限变化，以及实际子进程退出后的恢复；供应商在这些测试中是明确的协议模拟器，不调用付费服务。

## 本地识别实际验收

`test_harness_transcription_live.py` 默认跳过，设置 `HARNESS_REAL_ASR=1` 才运行。仅访问本机 `127.0.0.1:8012` 的已核验自托管识别服务；使用系统已安装的 Windows SAPI 生成无敏感测试语音，并使用随机隔离 PostgreSQL schema。没有调用云端语音合成或付费模型。`HARNESS_LIVE_ASR_EVIDENCE` 可指定保存原音频和带真实时间码的结果。

2026-10-23 的实际样本：7.320 秒英文系统合成语音，Whisper small int8 在 8.178 秒内返回三段正确句段，协调任务完成，字幕保持草稿。该证据只覆盖一次实际识别链，不能代表多语种、噪声、音乐、说话人或逐字对齐质量。
