# 模式 2 会话产物规范

完整会话使用 JSON 保存，顶层包含：

- `schema_version`、`mode_id`、`status`；
- `profile_snapshot`：本轮使用的固定研究画像；
- `evidence`：不少于 8 条带 URL、日期、来源类型、观察和相关性的证据；
- `candidate_questions`：6–12 个候选；
- `selection`：一个主问题、两个备选和最终问题契约；
- `decision_log`：候选被保留、缩小、改写或淘汰的理由。

每个候选必须包含：

- `candidate_id`、`research_question`；
- `observation_ids` 与 `closest_prior_ids`；
- `hypothesis`、`counter_hypothesis`、`falsification_rule`；
- `minimum_experiment` 和 `primary_risk`；
- 六个硬门槛；
- 重要性、新颖性、判别力、可执行性、可测量性、预期信息价值六项 0–2 相对评分及理由。

最终问题契约必须写清：最终问题、主假设、反假设、自变量、因变量、控制变量、最小实验、证伪规则、预期贡献、边界条件、算力假设和数据需求。

数字评分只用于候选间排序，不能替代来源证据或实验。验证器检查结构和引用一致性，不验证论文内容是否真实，也不替代新颖性判断。
