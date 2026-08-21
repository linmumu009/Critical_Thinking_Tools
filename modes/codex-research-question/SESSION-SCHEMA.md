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

## 历史会话

`schema_version=1.0` 的旧 JSON 来自已撤销的 Codex 自主流程。验证器会明确拒绝把它们当作共享流程的正式模式运行；文件保留只为历史追溯。
