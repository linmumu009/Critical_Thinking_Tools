# 共享研究问题发现会话规范 v2

完整会话使用 JSON 保存。模式 1 和模式 2 只能在 `execution` 中不同；其余流程字段与验证规则完全相同。

## 顶层字段

- `schema_version`：固定为 `2.0`；
- `pipeline_id`：固定为 `research_question_discovery_funnel_v1`；
- `execution`：`mode_id`、`engine`、`adapter_prompt`；
- `status`、`created_date`；
- `profile_snapshot`；
- `input_manifest`：本地文件、用户约束及其他真实输入；
- `stage_trace`：严格按阶段 0–8 排列；
- `evidence`、`candidate_questions`、`selection`、`decision_log`。

## 阶段轨迹

每个阶段记录：

- `stage_id`；
- `status`；
- `output_summary`；
- `artifact_refs`；
- `tool_trace`。

完整会话必须包含九个已完成阶段，顺序和 `tool_trace` 必须与 [pipeline-stages.json](pipeline-stages.json) 一致。这样可以阻止某个引擎跳过 QFT、STORM、双向钢人、现实试探或其他共享步骤。

## 证据

每条证据必须包含：

- `evidence_id`；
- `source_title`、`source_type`、`source_location`；
- `published_date`、`checked_date`；
- `observation`、`interpretation`、`unknown`。

本地文件使用绝对路径，在线来源使用 HTTP(S) 地址。搜索摘要不能替代一手来源。

## 候选问题

每个候选必须包含：

- 问题、现实信号引用、最近工作引用、问题家族与机制/证据簇；
- 决策分叉：合理答案 A/B/未知、相应行动与反转条件；
- 可执行证据路径；
- 廉价试探、各种结果的保留/缩小/改写/淘汰规则和实际处置；
- 五项硬门槛；
- 六项 0–2 评分及理由。

硬门槛来自原评分卡：

1. 有真实来源；
2. 合理答案导致不同动作；
3. 不预设答案；
4. 有现实可行的回答路径；
5. 伦理安全。

评分维度为决策杠杆、判别力、现实依据、可回答性、新颖非冗余和成本收益。评分只能比较已过硬门槛的候选，不能代替现实试探。

## 最终选择

完整结果选择一个主问题和两个不同的备选。三个入选问题都必须通过全部硬门槛，且廉价现实试探不得为 `reject`。

主问题契约必须包含：最终问题、触发信号、使用者与决策、期限、边界、竞争答案、行动映射、判别性证据、反转结果、最小试探、成本风险伦理、停止条件和残余未知。

## Runner sidecar

正式 session 的 v2 顶层 schema 保持不变。Runner 另外维护三个 sidecar，避免破坏历史兼容：

- `run-state.json`：当前阶段、checkpoint 哈希、失败与恢复记录；
- `evidence-ledger.json`：检索式及用途、来源纳入/排除、前三候选先行研究 collision review 和常识性/非平凡性审查；
- `completion-manifest.json`：结构/语义/账本审计结果及交付文件 SHA-256。

阶段 envelope 中的 `payload` 保存完整阶段推理产物，`session_updates` 只能修改该阶段允许的正式 session 字段。Runner 会拒绝跳阶段和越权修改。新 Runner 运行 finalize 时必须通过 sidecar 审计；旧 v2 正式结果仍可只用 session validator 校验。

Runner v1.1 要求每个最终入选问题额外通过两项 sidecar 门槛：

- 常识审查必须记录显然基线、残余不确定性、反例或边界以及证据；只有 `nontrivial` 或 `context-dependent` 可以入选；
- 先行研究审查必须引用三类检索（`exact-question`、`mechanism`、`adjacent-terminology`）和最接近证据；只有 `no-direct-match-found` 或有明确非冗余增量的 `incremental` 可以入选。

## 历史会话

`schema_version=1.0` 的旧 JSON 来自已撤销的 Codex 自主流程。验证器会明确拒绝把它们当作共享流程的正式模式运行；文件保留只为历史追溯。
