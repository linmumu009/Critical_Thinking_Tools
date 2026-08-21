# 模式 2：Codex 直接盲审协议

日期：2026-08-21

状态：在首次直接运行前冻结；尚未开始评分

## 身份

本项目的两种运行方式是：

1. **模式 1：API 自动模式**——外部模型 API 执行双评审和分歧仲裁；已完成并冻结。
2. **模式 2：Codex 直接模式**——当前 Codex 在本地工作区直接读取条件盲任务、生成结构化评分并写入独立进度；不调用外部模型 API，不要求用户或其他人工评审填写表格。

模式 2 不是“第二套 API”，也不读取任何 API Key、URL、模型配置或模式 1 的评分结果。

## 固定输入与隔离

- 使用与模式 1 相同的 48 单元、384 候选条件盲包，packet hash 固定为 `4e0cc330097015365c83c168a0cd8f763ca01b7f36af1254495e54c29eed84b7`。
- 调度种子固定为 `20260826`。
- 任务只含公开案例、E1-E6 证据目录和 C1-C8 候选问题。
- 在全部直接评分锁定前，模式 2 不读取 `automated-review-v0.4/`、原自动映射、生成条件、模型种子、隐藏事实、正确行动或下游成绩。
- 模式 2 不导入 API 调用模块，不读取 `model-config.local.json`，结果固定写入 `codex-direct-review-v0.4/`。
- 解盲密钥只在 48 个单元全部完成后用于本地敏感性重算；解盲后不再修改任何直接评分。

## Codex 评分规则

Codex 对每个候选输出五个字段：

1. `mapped_evidence_id`：只有某一个 E1-E6 能单独、完整回答候选要求的对象、比较、切分、时间和指标时才映射；需要组合、部分相关或缺少数据时为 `NONE`。
2. `atomic_single_observation`：只要求一个可独立回答的观察、比较或检验为 1，否则为 0。
3. `fully_answerable_by_mapping`：E1-E6 时必须为 1，`NONE` 时必须为 0。
4. `distinct_from_other_candidates`：与同组其他候选在证据和机制上不重复为 1，否则为 0。
5. `action_discriminating`：至少一种合理答案可能改变两个以上公开行动的相对支持为 1，否则为 0。

不得选择“最接近”的目录项，不得假定目录未承诺的联表、交叉分组、额外字段或因果识别。

## 无人工队列流程

1. 协调程序按固定顺序生成下一份条件盲任务。
2. 当前 Codex 直接完成该任务，并把结构化 JSON 写入临时响应文件。
3. 协调程序验证 packet hash、当前 review ID、C1-C8、合法映射、二元值和映射—完整可答一致性，再原子保存。
4. Codex 自动继续下一单元；用户不需要逐项查看、复制或确认。
5. 48 个单元完成后，本地程序锁定直接评分、解盲并重算候选指标与推进门槛。

任何无效响应都必须由 Codex 修正后重新提交；程序不能猜测或静默补值。已经锁定的单元不能覆盖。

## 输出

`codex-direct-review-v0.4/` 包含：

- `codex-direct-progress.json`：固定顺序和逐单元锁定评分；
- `codex-direct-final.csv`：384 行直接评分；
- `consensus-mappings.csv`：解盲后的原映射与 Codex 直接映射；
- `codex-direct-results.json`：机器可读敏感性结果；
- `CODEX-DIRECT-REPORT.md`：结论和边界。

## 命令

统一入口在未指定模式时先询问：

```powershell
python audit_mode.py run
```

显式准备模式 2：

```powershell
python audit_mode.py run --mode 2
python codex_direct_audit.py status
python codex_direct_audit.py next
python codex_direct_audit.py submit --response path-to-codex-response.json
python audit_mode.py finalize --mode 2
```

这些命令不调用外部模型 API。实际评分由当前 Codex 在任务中连续完成，而不是交给用户填写。

## 比较边界

API 模式和 Codex 直接模式只有在各自完成并锁定后，才允许由 `compare_api_and_codex_audits.py` 读取最终 CSV/JSON。比较器不参与评分，也不能反向修改任何结果。两种自动处理的一致不等于外部金标准，不一致则表示结果对执行后端敏感。
