# API 模式 2：契约—证明—反例隔离审计协议

日期：2026-08-20

状态：在 API 运行前冻结；实现完成，尚未调用 API

## 目标

模式 2 用一条与模式 1 不同的自动链路检查候选问题能否被证据目录完整回答：先在看不到目录的情况下提取问题要求，再让另一个角色证明单个目录项覆盖全部要求，最后由反例验证器尝试推翻契约或证明。

模式 2 不是模式 1 的第二轮提示，也不把模式 1 的共识当答案。它独立运行、独立保存、独立失败；只有两套模式都锁定后才能做只读比较。

## 固定输入与隔离边界

- 共同只读输入仍是 48 单元、384 候选的盲包；packet hash 为 `4e0cc330097015365c83c168a0cd8f763ca01b7f36af1254495e54c29eed84b7`。
- 模式 2 不读取 `automated-review-v0.4/`、模式 1 的进度、评分、仲裁或报告。
- 模式 2 不使用 `audit_judge_a_*`、`audit_judge_b_*`、`audit_arbitrator_*` 或主模型 URL/密钥/模型名。
- 模式 2 只读取独立的 `model-config.mode-2.local.json`，且文件必须包含 `api_audit_mode: 2` 身份标记；即使误传模式 1 配置文件也会在 API 调用前拒绝。
- 在该独立文件中填写公共的 `url`、`api_key`、`model_name`，或为 Extractor、Prover、Falsifier 分别完整填写角色覆盖槽位；不得回退到主模型或模式 1。
- 运行输出固定在 `automated-review-mode-2-v0.4/`。
- 调度种子固定为 `20260825`；Extractor、Prover、Falsifier 的角色种子段分别为 `710000`、`720000`、`730000`。
- 解盲密钥只在全部 API 阶段完成后用于本地敏感性重算；任何 API 消息都不含原映射、生成条件、种子、隐藏事实、正确行动或下游结果。

## 三阶段链路

### 1. Contract Extractor

输入只有公开简报、公开行动和候选问题。公开案例中的 `evidence_capabilities` 在此阶段被删除，Extractor 看不到 E1-E6 或可用数据字段。

每题输出：

- 一个或多个必须同时满足的原子证据要求；
- 是否为单一观察；
- 是否与同组候选实质独立；
- 是否可能改变行动相对支持。

### 2. Coverage Prover

输入只有上一步锁定的契约、E1-E6 目录和公开数据能力，不看原候选文本。Prover 必须为每个候选输出 E1-E6 各自覆盖的 requirement 索引矩阵；只有至少一个目录项覆盖全部要求时才能映射 E1-E6，否则必须输出 `NONE`。

程序验证矩阵恰好包含全部六个目录项、索引不越界；E 映射必须由该项的完整覆盖证明支持，`NONE` 则要求六项都没有完整覆盖。这样 Falsifier 能逐项检查证明，而不是接受一个没有来源的“最接近匹配”。

### 3. Counterexample Falsifier

输入候选原文、锁定契约、目录、覆盖证明和公开数据能力。它检查：

- 契约是否遗漏候选要求的对象、指标、比较、切分、时间或操作；
- 证明是否擅自假定联表、额外字段、因果识别或多个目录项组合；
- 候选的原子性、同组独立性和行动判别力。

Falsifier 只能接受 Prover 的映射或将其否决为 `NONE`，不能未经新证明改映射到另一个 E 项。最终 `fully_answerable_by_mapping` 由 E/NONE 确定性派生。

## 运行与格式规则

- 三阶段严格依赖，不能并行越过上游锁定输出。
- 每个角色每单元最多一次纯格式修复；第二次失败立即停止并保存失败。
- 每完成一个阶段即原子保存，重复命令只续跑缺失阶段。
- 模式 2 默认温度为 0、`max_tokens=3072`、`enable_thinking=false`；这些参数只能通过 Mode 2 专用字段调整并写入安全进度。
- URL、API Key、Authorization 头和完整本地配置不得写入结果或 Git。

## 决策与边界

最终映射按与模式 1 相同的候选指标和预注册推进门槛重算：

- 与原自动匹配差异率 `>= 0.10` 或出现门槛翻转：`fix_mapping_interface_before_gq2`；
- 差异率 `< 0.10` 且无门槛翻转：`proceed_to_gq2_generator_development`。

模式 2 不使用模式 1 的结果来作出本模式结论。跨模式一致率只由 `compare_api_audit_modes.py` 在两边完成后计算，它是稳定性诊断，不是真值投票。

## 输出

`automated-review-mode-2-v0.4/` 包含：

- `mode-2-progress.json`；
- `contracts.json`；
- `coverage-proofs.json`；
- `falsifier-verdicts.json`；
- `mode-2-final.csv`；
- `consensus-mappings.csv`；
- `mode-2-results.json`；
- `MODE-2-REPORT.md`。

## 命令

```powershell
python api_audit.py run --mode 2
python api_audit.py finalize --mode 2
```

不指定 `--mode` 时，统一入口必须在运行前要求选择模式。
