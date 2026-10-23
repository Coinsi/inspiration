# 视频 Agent Harness：当前实现与验收边界

更新：2026-10-23。本文描述当前代码，不替代[设计基线](README.md)，也不把模型表现、供应商能力与编排机制混为一谈。

## 1. 当前可以怎样评价

系统已经具备应用持有状态的生产运行层：同一创作对话承载计划、受控并行任务、持续指导、暂停续跑、工程候选、审阅、真实媒体处理及结果验收。工程与工具不依赖某一个模型会话，换文字模型不会自动重做已完成结果。

**这还不等于已经验证“一句话自主生成高质量五分钟剧情片”。** 当前可重复的端到端用例使用脚本决策器驱动真实数据库、工具和 FFmpeg：并行制作画面与音频，提出可编辑剪辑候选，局部调整配乐音量，暂停再继续，实际解码输出并由用户采用。它验证编排、工程和媒体执行链，不能证明真实模型能够稳定编剧、指挥表演、判断打斗质量或维持长片角色一致性。

请区分四个层次：

| 层次 | 当前状态 | 不应由此推导的结论 |
| --- | --- | --- |
| 状态与授权机制 | 已有数据库状态、版本锚点、事件、任务依赖、资源账本和写入隔离 | 不是无限并发、无限上下文或任意自动合并 |
| 确定性媒体与工程执行 | 图片调整、音视频裁剪/抽取、分层合成、音轨、字幕及时间线预览已有实际执行与测试 | 裁剪/蒙版合成不能代替语义重绘、生成延长或自动配音 |
| 外部模型/服务适配 | 文字、图片、视频、转写具有明确能力与配置入口 | 每一家上游、每一个模型、所有参考素材类型并未都做付费实测 |
| 创作质量 | 有固定产物、观察证据、验收条件和人工审阅的机制 | 没有已校准的审美评分器，也没有五分钟剧情片质量基准通过证据 |

## 2. 已落地的系统能力

| 能力 | 当前实现 | 主要代码 |
| --- | --- | --- |
| 单一对话入口 | 默认创作对话进入 production harness；直接生成图片/视频也构造同一运行层的工具任务。旧对话执行路径作为配置退路保留 | `conversation/production.py`、`conversation/service.py`、`workbench/ProductionTask.tsx` |
| 计划和多 Agent | 结构化计划、父子任务、依赖图、并行领取、任务/深度/动作限额；子任务只能继承或收窄对象和工具权限 | `harness/service.py`、`runtime.py`、`plan_contracts.py` |
| 稳定上下文 | 固定媒体哈希、文档修订、技能版本；保存用户决定及其替代/撤回关系；按任务构建上下文包 | `context.py`、`media_context.py`、`runtime.py` |
| 真正观察媒体 | 图片及裁剪图、视频抽帧、音频片段、转写文本作为不同证据；依据文字模型已声明的输入能力传递真实媒体 | `media_context.py`、`kernel.py`、`kernel_modalities.py` |
| 运行中意见 | 普通输入在单一活跃运行中记入意见收件箱；只读影响评估区分讨论、提问、局部、整体和撤回；不明确就询问，整体调整确认后执行 | `guidance.py`、`conversation/production.py` |
| 暂停、取消、续跑 | 停止新派发，已提交任务可保存回执；租约与旧修订防止迟到结果重写现状态；显式暂停不会被指导、审阅或服务重启解除 | `service.py`、`runtime.py`、`job_wait.py` |
| 工具治理 | 按需发现、严格输入、能力/副作用声明、固定工具版本与契约、统一调用回执、权限及费用检查；工具可以被非 Agent 客户端使用 | `modules/tools/`、`plan_contracts.py`、`execution_guard.py` |
| 可用模型发现 | 读取当前项目及当前用户的模型、默认选择、参数与参考输入能力；不联网、不返回凭据，具体组合交给估价校验 | `harness/model_tools.py`、`generation/service.py` |
| 工程候选 | 固定旧基线也能保存候选；读前提、规范命令、写范围及来源持久化；采用依然校验当前版本；仅安全、无冲突的操作允许受控重放 | `engineering/service.py`、`engineering/collaboration.py` |
| 反馈定位与选择性修改 | 固定工程/文档版本、实例路径和时间映射；重叠/多次使用明确返回歧义；有限的引用替换与撤销产生新候选，保护后来手改 | `feedback_tools.py` |
| 成果验证 | 文件完整性与完整解码、尺寸/时长/音轨要求、固定观察证据的创意检查、显式人工候选审阅；父任务聚合实际子任务/依赖成果 | `technical_validation.py`、`quality_tools.py`、`evidence.py`、`verification.py` |
| 资源治理 | 同一运行共享生成点数、模型 tokens、输出存储、媒体传输及其他限额；持久预留，未知用量不按零退还，实际用量回来后结算 | `resources.py`、`service.py`、`generation/jobs.py` |
| 结果接收恢复 | 已知原始上游任务句柄可以重新查询和下载；不重新提交生成；未知是否提交成功时不伪装成安全重试 | `recovery_tools.py`、`job_wait.py`、`generation/jobs.py` |
| 执行器替换 | 暂停并满足租约边界后显式更新内核/模型，保留任务、成果、授权与意见历史；工具契约漂移需要重新验证 | `kernel_control.py`、`plan_contracts.py` |

上表中的 Python 路径以 `backend/app/modules/` 为基准；前端路径以 `frontend/src/components/` 为基准。公开运行 API 位于 `/api/v1/projects/{project_id}/production`。

### 实际工具范围

| 请求 | 已有入口/效果 | 当前限制 |
| --- | --- | --- |
| 从素材库找可用内容 | 受权限约束的检索、固定引用、读取内容和已有标注/索引 | 检索质量取决于现有索引、标注与媒体理解；没有把所有未索引视频自动完整看完 |
| 选择可用生成模型 | `media.models` 返回项目和当前用户个人的可用目录、默认及建议选择 | 目录是本地配置声明，不等于联网实测；多线路能力并集不证明任意参数组合可行，仍须 `media.estimate` |
| 生成图片/视频 | `media.estimate`、`media.generate`、`media.job_observe` | 以供应商声明和实际路由能力为准；图片/视频/音频参考支持必须逐项满足，不按模型名称推断 |
| 调整已有图片 | `media.process` 的 `image.adjust`：裁剪、直角旋转、缩放、亮度/对比度 | 是确定性像素处理，不会理解“眼神更坚定”或重新设计武器 |
| 抽帧、裁视频、抽音轨 | `video.frames`、`video.trim`、`video.extract_audio` | 固定源和范围；现处理源上限 256 MiB、30 分钟，单次最多 12 个抽帧位置 |
| 多图、蒙版、透明度合成 | `engineering.compose` 生成可编辑 `image.document` 候选 | 当前原生层合成支持整数位移、透明度/蒙版；不支持任意图层变换或语义补画 |
| 音频裁剪、增益、静音、混合 | `engineering.compose` 的 `audio.document` | 原生音频执行器的能力有限；淡入淡出在 sequence 的实际渲染链中支持；不会生成新对白/音乐 |
| 拼接、转场、配乐、叠加图/视频、定时字幕 | `engineering.compose` 的 `sequence.timeline`，预览为真实渲染任务 | 转场与叠加以当前 schema 为准；没有完整 AE 级特效图、任意动画曲线或插件兼容层 |
| 音视频转写 | `media.transcribe`、`media.transcription_status`，分段检查点、固定源、草稿结果 | 需要自托管转写服务及明确计费策略；句段时间不是逐字强制对齐；不自动发布到已采用字幕 |
| 用户说“这段改一下” | `engineering.feedback` 解析已固定的预览时间/实例，再建立候选 | 不能把旧预览的时间默默套到后来修改的时间线上；嵌套/重叠无法唯一定位时先询问 |
| 只采用某部分、撤销某次引用替换 | `engineering.selective_edit` | 目前针对原生文档输出中的直接 source/mask 引用替换；不是任意参数、任意时间线结构的通用 cherry-pick/undo |

### 尚未接通的具体创作算法

- **语义局部重绘**：旧领域已有受限的资产/镜头蒙版重绘接口，但尚未成为具备稳定请求、预算和恢复契约的 harness 工具；不能声称 Agent 已可随意修改角色眼睛或武器细节。
- **生成式视频编辑/延长**：没有通用的延长、换动作、局部替换连续画面的执行器；视频参考输入能力不等于完整视频编辑能力。
- **配音、音乐、音效生成与对口型**：没有已接通的通用执行器。用户现有音频可以作为输入进行确定性编辑。
- **逐字对齐和动态歌词自动设计**：有转写、定时字幕与合成底座，没有逐字强制对齐及自动歌词动画制作的完整链。
- **复杂特效**：没有跟踪、转描、粒子、完整节点合成、复杂关键帧动画等通用执行器；工程可扩展不等于这些算法已经存在。
- **长期角色/道具与剧情连续性质量**：可以记录约束、参考、证据和人工例外，但没有经过真实作品基准验证的全自动一致性保证。
- **大规模生产保障**：没有几百 Agent 并行、跨大量租户高负载、生产级费用对账和容灾演练的验收证据。当前限额是主动收敛规模，不是已验证的吞吐指标。

## 3. 基线 15 个反例：代码验收映射与边界

下面的函数名是具体复查入口，均在 `backend/tests/`。映射说明覆盖了什么，**不把“存在测试”当成“全量回归已经通过”**；执行结果单独记在第 6 节。

| # | 反例及当前保证 | 测试文件 / 函数 | 明确边界 |
| --- | --- | --- | --- |
| 1 | 后续对话不是只拿到媒体 ID；可将固定图片/视频帧/音频送给声明支持的模型，转写与听音分开标记 | [test_harness_context.py](../../../backend/tests/test_harness_context.py) `test_actual_image_crop_and_cross_project_denial`、`test_video_frames_audio_and_transcript_have_explicit_coverage`、`test_text_only_audio_context_does_not_claim_listening`；[test_harness_conversation.py](../../../backend/tests/test_harness_conversation.py) `test_completed_media_is_visible_and_followup_receives_fixed_reference_not_fake_artifact` | 抽样观察不等于完整观看；文本模型无音频能力就不能声称听过；上下文有明确预算 |
| 2 | 局部意见保留无关任务，关联下游失效；多个父节点分别替代，父子链不重复替代，整体调整需确认 | [test_harness_guidance.py](../../../backend/tests/test_harness_guidance.py) `test_local_scope_fences_late_work_but_preserves_sibling_and_user_pause`、`test_global_change_requires_idempotent_human_confirmation`；[test_harness_audit.py](../../../backend/tests/test_harness_audit.py) `test_steer_multiple_parents_keeps_unique_children_and_exact_dependencies`、`test_steer_leaf_invalidates_ancestor_consumers_but_preserves_siblings` | 结构化影响评估有证据和范围校验；自然语言判断本身仍受模型能力影响，歧义不能自动全量重做 |
| 3 | 意见替代/撤回持久化，旧决定和依赖它的摘要不复活 | [test_harness_guidance.py](../../../backend/tests/test_harness_guidance.py) `test_replace_then_retract_does_not_resurrect_earlier_user_instruction`；[test_harness_context.py](../../../backend/tests/test_harness_context.py) `test_replaced_then_withdrawn_decision_does_not_resurrect` | 撤回规则针对已记录决定及依赖；没有反推并删除所有历史生成文件 |
| 4 | 无冲突静态候选可重放；读依赖或时长/同步相关变更不能被忽略 | [test_harness_engineering.py](../../../backend/tests/test_harness_engineering.py) `test_independent_native_changes_merge_but_same_document_conflicts`、`test_input_dependency_change_blocks_other_output_replay`、`test_timed_document_requires_explicit_integration_review` | 同一文档及时间同步采取保守冲突；没有通用自动合并或自动重新对齐配音/字幕算法 |
| 5 | 旧基线候选可以晚到保存，但不能覆盖用户新版本或另一个对话刚采用的版本 | [test_harness_engineering.py](../../../backend/tests/test_harness_engineering.py) `test_late_timeline_retains_old_input_and_cannot_replace_manual_edits`；[test_harness_audit.py](../../../backend/tests/test_harness_audit.py) `test_two_conversations_same_work_keep_late_candidate_and_protect_adopted_version` | 需要重新整合和审阅的冲突会明确阻止采用，不做“尽量合并” |
| 6 | 暂停不因迟到回执、重启或指导解除；服务故障回到持久运行状态，问题回复仍属于原运行 | [test_harness_runtime.py](../../../backend/tests/test_harness_runtime.py) `test_pause_allows_receipt_but_prevents_next_dispatch_until_resume`、`test_repeated_pause_keeps_original_resume_state`；[test_harness_conversation.py](../../../backend/tests/test_harness_conversation.py) `test_plan_confirmation_and_question_reply_share_same_production` | 恢复依赖 worker/数据库可用；未知外部提交不会自动重新付费派发；暂停不保证能撤销上游已受理任务 |
| 7 | 丢失本地回执时恢复原任务；已知上游句柄重新查询/下载，接收失败不重生成 | [test_harness_job_wait.py](../../../backend/tests/test_harness_job_wait.py) `test_lost_submission_receipt_recovers_exact_attempt_without_dispatch`；[test_harness_recovery_tools.py](../../../backend/tests/test_harness_recovery_tools.py) `test_original_handle_is_polled_and_saved_once_without_submission`、`test_download_failure_can_repeat_reception_with_new_key_and_no_second_charge`、`test_uncertain_submission_or_upstream_failure_is_never_reissued` | 没有句柄、无法证明提交状态或上游已明确失败时，不能包装成接收恢复；第三方查询/取消能力取决于适配器 |
| 8 | 取消与成功竞态保存已有回执/费用线索，但旧任务不推进新流程 | [test_harness_runtime.py](../../../backend/tests/test_harness_runtime.py) `test_cancel_fences_late_results_and_retains_receipt`；[test_harness_job_wait.py](../../../backend/tests/test_harness_job_wait.py) `test_superseded_task_does_not_publish_late_result`；[test_harness_transcription_runtime.py](../../../backend/tests/test_harness_transcription_runtime.py) `test_control_cancel_mid_recognition_fences_late_cues_and_successor` | 上游取消不保证退款；不删除已经产生的成果 |
| 9 | 成功状态、模型自述、元信息不能代替实际可解码文件和验收证据 | [test_harness_validation_stage.py](../../../backend/tests/test_harness_validation_stage.py) `test_runtime_requires_real_full_decode_before_delivering`、`test_bad_delivery_fails_without_regenerating_or_charging`；[test_harness_quality_tools.py](../../../backend/tests/test_harness_quality_tools.py) `test_metadata_cannot_prove_visual_and_missing_or_negative_findings_do_not_pass`、`test_quality_cannot_borrow_unrelated_same_run_or_before_reopened_boundary` | 技术通过不是艺术质量通过；有限观察不足以满足完整覆盖条件；未完成检查不能按通过处理 |
| 10 | 用户旧预览位置绑定固定修订、实例和时间映射；重叠或多次使用返回多个候选位置 | [test_harness_feedback.py](../../../backend/tests/test_harness_feedback.py) `test_feedback_loop_reverse_range_and_old_revision_never_follows_current`、`test_sequence_overlap_returns_both_occurrences_and_still_frame_is_not_video_time`、`test_nested_instance_path_uses_each_fixed_time_map` | 没有足够锚点时必须重新定位；不能靠一句“这里”猜当前时间线 |
| 11 | 有限的局部选用/撤销保留无关手改；时间同步不通过时拒绝自动采用 | [test_harness_feedback.py](../../../backend/tests/test_harness_feedback.py) `test_select_one_candidate_reference_and_undo_preserves_other_manual_edits`、`test_undo_refuses_to_clobber_new_reference_and_selection_cannot_apply_unrelated_parameter`；[test_harness_engineering.py](../../../backend/tests/test_harness_engineering.py) `test_timed_document_requires_explicit_integration_review` | “v2 画面 + v1 配音”的任意跨版本混编不是通用一键能力；须构建新的同步候选并验证，不能跳过时间约束 |
| 12 | 缺工具、缺权限、版本漂移、不支持参考/效果不静默降级 | [test_harness_plan_contracts.py](../../../backend/tests/test_harness_plan_contracts.py) `test_missing_tools_bad_arguments_and_effect_escalation_fail_before_dispatch`、`test_reference_support_checked_and_fixed_hash_preserved_without_generation`；[test_harness_editing_tools.py](../../../backend/tests/test_harness_editing_tools.py) `test_native_unsupported_effects_rejected_and_catalogue_truthful`；[test_harness_process_tools.py](../../../backend/tests/test_harness_process_tools.py) `test_capability_inventory_does_not_claim_unimplemented_generation` | 计划能冻结 schema 和能力；工程配方的深层校验仍在执行前完成，不能把浅预检当成已渲染成功 |
| 13 | 预算共享、预留持久化、未知用量不清零；循环/动作/重试/时间受限；输出超硬限不写部分 CAS | [test_harness_resource_ledger.py](../../../backend/tests/test_harness_resource_ledger.py) `test_restart_keeps_unknown_reservation_nonzero_and_measured_difference_is_reusable`、`test_concurrent_workers_lock_shared_run_and_cannot_overreserve`、`test_oversized_provider_batch_writes_no_partial_cas_results`、`test_limits_update_preserves_pause_and_cannot_change_effects_or_points`；[test_harness_resource_and_review.py](../../../backend/tests/test_harness_resource_and_review.py) `test_run_action_ceiling_is_shared_not_reset_by_another_worker` | 点数不等于真实货币对账；传输账本不代表供应商/CDN 总账；已发生实际超额要记录并阻止下一步，不能倒退抹掉费用 |
| 14 | 调用、排队后执行、落库前重新检查权限及源引用；冻结配置发生漂移不隐式换服务 | [test_harness_job_authorization.py](../../../backend/tests/test_harness_job_authorization.py) `test_live_revocation_before_execution_prevents_any_provider_call`、`test_revocation_after_provider_finishes_prevents_cas_and_publication`、`test_permission_revoked_during_cas_blocks_final_database_publication`；[test_harness_recovery_tools.py](../../../backend/tests/test_harness_recovery_tools.py) `test_worker_rechecks_configuration_permissions_and_handle_after_dispatch` | 已提交到外部的请求可能无法撤回；恢复必须重新满足当前权限和原句柄契约 |
| 15 | 替换内核/文字模型保留系统事实，作废尚未执行的旧决策，权限不会随换模型扩大 | [test_harness_kernel_control.py](../../../backend/tests/test_harness_kernel_control.py) `test_switch_keeps_completed_work_and_invalidates_only_unexecuted_agent_decision`、`test_switch_requires_explicit_pause_and_settled_lease`、`test_invalid_kernel_switch_is_atomic_and_never_falls_back`；[test_harness_audit.py](../../../backend/tests/test_harness_audit.py) `test_frozen_tool_drift_blocks_real_runtime_before_handler` | 新内核仍须实现本系统动作契约；不是任意第三方 Agent SDK 即插即用，也不自动迁移未知工具 schema |

## 4. 在一个创作对话中怎么用

1. **进入项目工作台的创作对话。** 在同一输入框讨论想法、添加系统内容、引用技能或直接生成图片/视频。已有作品可关联到对话；工程操作必须归属正确作品。无需另开一套“图片对话”或“视频对话”。
2. **复杂制作先看方案。** 例如“用这张图和这段音乐做一个两秒可编辑片段，音量降低六分贝，先让我审阅”。方案卡显示步骤和本次生成点数上限；点击“确认方案，开始制作”。预算为零不调用付费生成。明确的直接生成请求使用同一 harness，通常不再强制额外方案确认，但仍受模型能力、权限与预算检查。
3. **并行工作在同一进度卡查看。** 依赖允许的任务会并行执行，依赖结果的剪辑步骤等待；用户不需要逐个打开子 Agent 对话。当前默认并行上限 3，配置最大 8；这是受控运行规模，不是百 Agent 控制台。Agent 步骤并行与媒体执行器容量分别受控：本机 Windows 部署当前只有一个 `solo` 生成 worker，实际生成/渲染作业可能排队，不应将任务并行上限宣传成同时生成数量。
4. **随时补充指导。** 单一活跃运行时继续输入普通消息；也可点某一步“调整”，明确“只改配乐，画面保留”。系统先记录意见并评估影响，在未确定范围期间阻止该运行新增付费或工程写入。需要定位/澄清就回答问题；整体重做会显示“确认整体调整”。同一对话有多个活跃运行时要明确目标，系统不能猜。
5. **暂停与停止分开。** “暂停”停止派发后续步骤，“继续”恢复；已经受理的生成可能还在返回。“停止”取消本次后续制作，保留已有成果；是否停止远端计算或退款取决于上游。暂停中提交指导或调整资源不会自动恢复。
6. **候选需要实际审阅。** 出现“预览并审阅候选”后进入该作品的固定候选。可预览、采用或拒绝；采用发生版本/依赖冲突时需要重新整合。审阅结果回传原运行，只有相关等待步骤被唤醒；显式暂停继续保留。Agent 的“完成”不代表自动覆盖当前采用版本。
7. **失败时按恢复类型操作。** 可安全重试的步骤显示“重试”；已生成但下载/接收失败的步骤可显示“重新接收结果”。后一操作只查询原任务及接收原产物。未知是否提交成功不会自动重生成或再次计费。网络断开时“重新连接”只刷新进度。
8. **用资源面板检查预留和实测。** 展示模型 tokens、生成文件、媒体传输的实测/预留及剩余额度。允许的等待/暂停状态下可以修改本次限额；不会由此增加采用权限或生成点数授权。资源限制解除后仍要按状态继续/重试。
9. **暂停时可更换后续文字模型。** “模型设置”中应用最新配置或另一个可用文字模型，完成后仍保持暂停。已完成成果保留，尚未执行的旧模型决策重新生成。不是从头再做整个项目。
10. **离开页面后回来查看同一运行。** 浏览器不是任务状态的持有者。后台需要保持运行；服务恢复后读取数据库、租约、候选与原上游句柄。等待用户的步骤不会因反复轮询就继续制作。

“把主角眼神改坚定”“重新设计武器”“打斗更精彩”等意见可以记录、定位、评估影响，但是否能执行取决于第 2 节的具体算法和上游模型。尚无执行器时应明确缺口，不能用裁剪或一段说明冒充完成。

## 5. 外部前置、部署与存储保留

### 本机部署记录（2026-10-23）

- 实际代码目录为 `C:/Code/Python/inspiration`。API 使用 `http://127.0.0.1:8000`，本机前端当前端口为 `15173`（启动器在系统不允许绑定 5173 时选择备用端口）。
- 用户数据库 `public` 已由 0047 升至 `0053_harness_retention_guards`；升级前已完成 PostgreSQL 自定义格式备份，位置 `C:/Users/ASUS/.codex/backups/inspiration/public-before-harness-20261023-032007.dump`。该文件是数据库备份，媒体文件仍由原 CAS/MinIO 保存。
- API 和此仓库专属 Celery worker 已重新启动。`/health/ready` 返回 ok；数据库、存储、队列、生成 worker、转写/索引服务和 8 个应用后台 worker 均就绪，包括 production、工程交付和归档。
- 最终代码重启时间为 2026-10-23 04:31：API 父进程 22036、服务进程 61956，生成 worker 启动进程 86176；专属 worker 名称 `inspiration-0a747059f02d@Coinisi`。启动完成后独立 ping、active/reserved 和健康检查均通过。日志前缀 `.runtime/harness-final-api-20261023-043119` 与 `.runtime/harness-final-worker-20261023-043119`。这些 PID 是验收时快照，后续重启会变化。
- 升级前后原生成任务计数均为成功 28、失败 13、取消 1，没有在途生成任务；验收使用隔离 schema，未给真实项目写入测试 ProductionRun。
- 经进程路径和服务日志确认转写由本仓库 `.runtime/transcriber` 的本地 Whisper 执行，仅为它设置 `HARNESS_TRANSCRIPTION_COST_MODE=self_hosted`。这不是将任意远端识别服务声明为免费。

### 回退边界

新对话接管由 `PRODUCTION_HARNESS_ENABLED` 控制。需要临时回到旧执行入口时，先暂停仍在进行的新制作运行，再关闭该开关并重启 API；旧 AgentRun 和旧对话读取路径保留。开关不删除 ProductionRun，也不取消或重提已经受理的外部生成任务。

不要用数据库降级或恢复旧备份来代替应用入口回退：0050 的降级会删除新增运行记录，备份恢复还会覆盖升级后的业务数据。生产数据需要恢复时应先保留现状并单独核对数据库与媒体版本。当前备份用于部署保障，未对用户库执行恢复或破坏性降级。

### 启动前置

- PostgreSQL 必须迁移到当前头 `0053_harness_retention_guards`：0050 增加运行状态，0051 增加工程候选协作字段，0052 固定转写执行快照，0053 补全新表的 CAS 引用保护触发器。
- `PRODUCTION_HARNESS_ENABLED=true` 是当前默认。后台 production worker 由 `AGENT_WORKER_ENABLED` 启动；转写、素材、工程任务还有各自 worker。需要检查健康端点中的实际 worker 状态，不能用页面可打开推定生产任务可运行。
- 本地媒体操作/预览依赖可用的 FFmpeg/ffprobe、Python 图像处理库及可读写媒体存储；数据库连接可用不代表渲染依赖齐备。
- 真实文字/图片/视频任务需要用户配置的可用服务、凭据、模型能力与额度。具备视觉/音频输入能力必须明确声明，不能从“多模态”名字推断。
- 图生视频、视频参考等任务还需满足上游的输入传输契约。有的服务要求公网可访问链接；本地 MinIO 不会自动成为公网对象存储。是否支持直接文件/base64 由适配器决定，不能一概要求或假定 OSS。
- 自动转写需要 `TRANSCRIBER_URL`、转写 worker 和 `HARNESS_TRANSCRIPTION_COST_MODE=self_hosted`。未配置或费用策略未知时不派发；多数自动化测试使用受控识别器，另有本机 Whisper 的实际链路验收，不能将一条英语样例的通过推广成所有语言/噪声/长音频质量保证。
- 付费远端调用、部署环境性能和长期可靠性须另做受控验收。本材料不声称已经使用真实用户素材做过付费长片制作。

### 垃圾回收复核

已检查 `storage/retention.py`、`gc_guards.py`、`garbage_collection.py`、`lifecycle.py` 和 `engineering/archive_cleanup.py`：

- 保留根动态来自所有已注册模型的 Blob 外键和 JSON 内容，包含 `production_run`、`production_task`、`production_attempt`、`production_event`，也包含工程资源、候选、文档历史和提交记录。固定哈希作为值或 JSON 键都会被检查。
- 取消、任务被替代或运行完成，不会自动让上述来源记录失效或删除历史文件。**这偏向不丢数据，不是一套成熟的存储成本优化/自动过期策略。**
- 标记待删除之后，真正删除前会再次检查引用；出现运行固定引用会取消删除。数据库触发器阻止删除墓碑建立后再发布引用，读锁保护正在读取的媒体。
- 复核发现 0050 新增表原先没有安装引用触发器，导致 opt-in GC 在校验覆盖面时拒绝执行；0053 已补全安装。此前属于失效关闭，不是静默删掉素材。`storage_gc_enabled` 仍维持原默认 `false`，本次没有打开回收。
- 工程归档清理仅清理由该归档任务持有的私有暂存文件，校验路径和文件白名单，并避开运行/读取中的任务；不是删除工程引用的 CAS 源文件。
- [test_harness_retention.py](../../../backend/tests/test_harness_retention.py) 覆盖四类 production 固定引用在标记后的保留及 0053 触发器安装/墓碑拒绝；[test_engineering_retention.py](../../../backend/tests/test_engineering_retention.py) 覆盖候选/历史/其他租户；[test_engineering_gc.py](../../../backend/tests/test_engineering_gc.py) 覆盖读写竞态、幂等删除、重新上传和未装保护时拒绝回收。

### 扩展时必须保留的约束

新业务工具要同时提供严格 schema、能力和副作用声明、版本、作用范围、稳定请求、真实执行器、固定输入/输出、费用及恢复策略，不能只注册名称。新增长期任务必须桥接等待、回执、暂停/取消和租约检查；新增媒体格式要增加真实技术验收；新增工程效果要同时有 schema、执行器及验证能力，不能只在 JSON 中添加字段。具体接入接口与需要显式修改的生命周期连接点见 [EXTENSION.md](EXTENSION.md)，当前没有自动适配任意长期任务的通用 JobBridge。

工具/工程 schema 升级目前采取显式版本和拒绝漂移，没有承诺自动迁移任意未来插件。新引用表须纳入模型注册与 GC 触发器迁移；否则保守停止回收。长期保存、导出兼容、跨版本回放和清理策略仍需随新执行器补真实迁移案例。

## 6. 验收记录与尚待确认

- 资源账本与输出硬限初轮专用测试：`test_harness_resource_ledger.py` **21 passed**；真实 PostgreSQL 隔离 schema，输出超限验证 CAS 写入次数为零，未调用付费模型。收尾修复预览回执丢失后预算无法结算的问题，与等待恢复组合回归 **35 passed**；另对单步骤达到执行次数后提高限额可继续、暂停仍保留的相关用例专项验证 **2 passed**。这些运行有重叠，不能相加作为不重复用例总数。
- GC 初轮组合测试：新的四类 production 引用保留 + 工程保留/回收，**13 passed**；追加 0053 迁移后，`test_harness_retention.py` **5 passed**（其中 4 个与初轮重叠，不能把两次计数相加当成独立用例数）。新迁移在隔离 schema 中重现缺保护、安装全部触发器，再验证删除墓碑阻止新的运行引用。
- 综合 66 文件回归初轮：**461 passed / 2 failed**。失败为旧工具目录精确集合断言、端到端脚本 fixture 的最终产物选择。断言已更新，端到端用例独立复测 **1 passed**；不能把修复后的单项通过直接改写成综合全绿。
- 最新前端 `npm run build` 已通过。本机转写实际链路独立用例 **1 passed**：7.32 秒 Windows SAPI 英语音频经本机 Whisper 得到三句正确转写，识别约 8.178 秒。该样例是接口和执行链验收，不是 ASR 质量基准。
- 后续完整综合回归：75 个相关测试文件，**555 passed / 1 skipped**，660.22 秒，报告位于 `.runtime/harness-final-regression.xml`。跳过项为需显式开启的真实 ASR，已单独运行通过；该完整运行取代初轮 461/2 作为当前综合结果，各专项计数不与它累加。
- 浏览器验收包括真实隔离后端的方案确认、暂停/继续、回复、完成，以及个人/项目模型输入能力保存并刷新；另用明确标记的界面状态夹具检查指导确认、候选直达、资源和暂停换模型。深浅色及 390px 窄屏截图已复核。用户库已迁移至 0053，部署记录见第 5 节。
- 模型选择补齐：`test_harness_model_tools.py` **4 passed**，覆盖项目逻辑默认与隐藏线路、个人目录隔离和撤权、视频能力与参数限制，以及 `cloud_llm` JSON 动作协议中的 discover → models → estimate。文字响应是受控夹具，实际模型目录和估价服务运行，未创建生成任务或调用付费模型。
- **收尾可靠性补齐**：新 harness 远端生成已复用持久投递表，任务与 outbox 同事务保存；pending 可重投同一 Job，领取后失联只进入原句柄接收恢复或明确不确定状态，不自动重新付费提交。新增 `test_harness_remote_delivery.py` 6 项，与本地交付、原句柄恢复、执行权限组合 **48 passed**；独立 `test_harness_remote_recovery.py` **17 passed**。后一组包含真实 Python 子进程持久领取后 `os._exit(37)`，父进程通过新 Session 注入租约过期后恢复同一任务；外部供应商为协议夹具，检查 submit 为 0、只轮询原 ID、只保存并扣费一次。其他迟到轮询竞态为确定性多 Session 交错，不冒充真实断网测试。
- 最终修复后的 51 文件组合回归：**500 passed / 1 skipped**，554.04 秒，报告 `.runtime/harness-durable-model-regression.xml`。涵盖全部 harness 测试和相关旧生成、模型服务、工程预览、对话路径；跳过项仍为 opt-in 真实 ASR。与前述 555 项综合运行和专项运行有重叠，不相加作为独立用例总数。新增模块及受影响生成/交付代码 Ruff、主入口语法检查和 `git diff --check` 均通过；旧依赖弃用及 Decimal 序列化警告不作为测试失败隐去。
- 最终部署后前端 `/` 返回 200，受保护的 production API 经前端代理返回预期 401（未登录），控制 API 已包含 `resume_receipt`；服务健康和专属 worker 检查通过。当前工作区未提交/推送。
- 端到端证据入口：[test_harness_production_e2e.py](../../../backend/tests/test_harness_production_e2e.py) `test_single_chat_parallel_workers_replan_pause_render_and_human_adoption`。测试读取实际渲染像素与音频 RMS、时长和轨道，而非只断言状态字符串；使用脚本决策器，明确不评估创意质量。

后续创作验收需要覆盖真实文字模型的规划/指导判断，具体供应商的图/视频参考协议与故障恢复，真实 ASR，用户常见局部修改，以及多轮长作品的预算和质量。缺少这些证据时应保持“机制已实现，真实创作表现待验收”的表述。

## 7. 对话负载与多媒体传输补齐

本轮新增上游 SSE 接收、已鉴权的对话进展事件流、普通聊天轻量协议、工具目录和最多四项完整 schema 的按需加载，以及整体请求文本预算。模型流未完整结束不执行动作，预览只投影最终回复字段；取消、租约失效及跨项目访问均受检查。旧 Agent 路径也按需加载工具，避免继续发送完整目录定义。

图片参考按供应商协议使用 multipart 或 data URL，并校验大小、MIME 和扩展名。URL-only 视频通道支持显式配置签名素材公网入口；该入口必须访问同一份存储，本地开发并不会凭此自动拥有公网传输能力。未实现的供应商专属上传协议不能声称已经支持。真实供应商延迟、实际生成质量及公网入口连通性仍应单独验收。
