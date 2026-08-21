# Candidate Generation v0.4：全自动盲映射审计协议

日期：2026-08-20

状态：在 API 运行前冻结；全自动审计已完成

身份：本协议现固定为 [模式 1：API 自动审计](API-MODE-1-FROZEN-v0.4.md)。[模式 2](CODEX-DIRECT-MODE-PROTOCOL-v0.4.md)由当前 Codex 直接处理，不修改、不调用也不复用本协议的 API 评分链路。

## 目标与替代关系

项目不把人工参与设为候选映射复核的必经步骤。v0.23.0 的人工盲审包保留为可选外部复核材料，但当前工程路线改用全自动审计：两个隔离评审独立判断，第三个评审只仲裁分歧，随后程序自动重算候选指标和预注册门槛。

该流程回答的是“原自动匹配结论对另一组盲提示是否稳定”，不是人工确认或外部金标准。若所有角色使用同一个模型，报告必须保留相关语义偏差限制。

## 固定输入

- 使用 v0.23.0 已冻结的 48 单元、384 候选盲包；packet hash 必须为 `4e0cc330097015365c83c168a0cd8f763ca01b7f36af1254495e54c29eed84b7`。
- 不重新生成问题，不修改证据目录，不读取隐藏事实或下游结果。
- 只在最终敏感性计算阶段使用协调者密钥恢复 G0/GQ/GS/GB、模型种子和原自动映射。
- 评审调度随机种子固定为 `20260824`。

## 三个隔离角色

### Judge A：严格证据契约

逐字检查候选所需对象、比较、切分、时间和指标。只有单个目录项能够完整回答时才映射 E1-E6；部分相关、需要联表或需要组合证据一律 `NONE`。

### Judge B：反例审计

独立从“这个目录项为什么可能无法完整回答”出发，先尝试构造反例，再决定映射；同时检查问题是否复合、是否与同组问题实质重复、答案是否可能改变行动排序。Judge B 看不到 Judge A 的输出。

### Arbitrator：只处理分歧

仲裁器只看到公开案例、目录、候选和 A/B 不一致的字段。A/B 标签在每个单元中按固定随机规则交换为 X/Y。仲裁器不得看到原自动匹配，不得修改双方一致字段，输出必须恰好覆盖全部分歧。

## 固定评分字段

每个候选输出：

1. `mapped_evidence_id`：E1-E6 或 `NONE`；
2. `atomic_single_observation`：0/1；
3. `fully_answerable_by_mapping`：0/1，且必须与 E/NONE 一致；
4. `distinct_from_other_candidates`：0/1；
5. `action_discriminating`：0/1。

每次格式错误只允许一次不提供新案例信息的纯格式修复；第二次失败停止并保留失败记录。每完成一个角色即原子写入可恢复进度，不完整单元不能进入最终报告。

同一单元的 Judge A 与 Judge B 可以并行调用，但二者消息完全隔离；只有两份输出均保存后才计算分歧并调用仲裁器。并发只改变等待时间，不改变固定输入、提示、种子或判定规则。

## 模型配置

默认三个角色复用 `model-config.local.json` 的当前 API、模型和输出/思考预算，评审温度固定为 0。角色种子分别使用 `610000 + review_id`、`620000 + review_id`、`630000 + review_id`。

可在本地配置中为三个角色分别填写以下可选槽位：

- `audit_judge_a_url/api_key/model_name`；
- `audit_judge_b_url/api_key/model_name`；
- `audit_arbitrator_url/api_key/model_name`。

空槽位回退到主模型。任何 API Key、Authorization 头或完整本地配置都不得写入进度、结果、报告或 Git；只保存模型名和安全运行参数。

## 判定规则

自动评审 A/B 的映射完全一致率和 Cohen's kappa 仍作为提示稳定性诊断：

- 完全一致率 `< 0.85` 或 kappa `< 0.70`：`refine_automated_judge_protocol_before_gq2`；
- 在提示稳定的前提下，仲裁共识与原自动匹配不一致率 `≥ 0.10`，或任一预注册门槛翻转：`fix_mapping_interface_before_gq2`；
- 提示稳定、不一致率 `< 0.10` 且无门槛翻转：`proceed_to_gq2_generator_development`。

这些是工程路线闸门，不是显著性检验。下游主指标、准确率、成本和案例护栏保持冻结，只替换候选映射派生指标。

## 固定运行量与成本

- Judge A：48 个单元，通常 48 次调用；
- Judge B：48 个单元，通常 48 次调用；
- Arbitrator：只调用存在分歧的单元，0～48 次；
- 每个角色最多一次格式修复，因此最坏上限为 288 次，通常不超过 144 次。

## 输出

运行目录为 `automated-review-v0.4/`：

- `automated-audit-progress.json`：可恢复角色输出、格式偏离和安全参数；
- `judge-a.csv`、`judge-b.csv`：两份独立自动评分；
- `adjudication.csv`：只含分歧字段及最终值；
- `consensus-mappings.csv`：解盲后的原映射与自动仲裁共识；
- `automated-sensitivity-results.json`；
- `AUTOMATED-SENSITIVITY-REPORT.md`。

## 命令

```powershell
python automated_mapping_audit.py run
```

网络或进程中断后重复同一命令即可从已完成角色继续。全部角色完成后也可只重建报告：

```powershell
python automated_mapping_audit.py finalize
```
