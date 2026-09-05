# 完成顺序偏差最小探针

本探针承接模式 2 正式运行 05 的主问题，负责审计外部重放/训练实现产生的逐步梯度与轨迹有效权重。它不负责生成 rollout，也不会从合成数据推断真实模型效果。

## 四个条件

- `real`：按记录或注入的完成顺序消费轨迹。
- `within_version_random`：保持行为版本和固定轨迹集合，只在版本内随机置换轨迹身份。
- `version_balanced`：在可用版本间平衡取样。
- `version_sync_oracle`：按版本同步聚合的参照更新。

每个随机种子和时延相关性注入水平都必须包含全部四个条件。每个条件必须覆盖完全相同的轨迹 ID，更新步数和梯度维度必须一致；否则分析直接拒绝运行。

## 输入契约

输入为一个 JSON 对象：

- `schema_version`：固定为 `1.0`。
- `study_id`：实验标识。
- `scientific_status`：真实实验使用 `real_replay`；合成验收必须使用 `synthetic_pipeline_check_only`。
- `primary_injection_level`：用于主判定的时延相关性水平。
- `thresholds`：固定包含平均余弦差门槛、跨种子同方向要求和剂量反应 Spearman 门槛。
- `trajectories`：每条轨迹的 ID、prompt ID、行为版本、完成时间和持续时间。
- `runs`：每个种子 × 注入水平 × 条件的逐步梯度和逐轨迹有效权重。

当 `scientific_status` 为 `real_replay` 时，分析器额外强制 256–512 条轨迹、至少两个行为版本，以及每个单元 20–50 个连续更新步。

输出包括相对同步 oracle 的平均梯度余弦、相对范数误差、慢四分位累计有效权重，以及 `within_version_random - real` 余弦差的跨种子和剂量反应汇总。

## 预注册判定

只有同时满足以下条件才输出 `continue`：

1. 主注入水平的平均余弦恶化至少 `0.05`。
2. 三个随机种子的恶化方向一致。
3. 至少三个注入水平上，时延相关性与余弦恶化的 Spearman 相关至少 `0.8`。

任一条件不满足即得到 `stop_or_narrow`，三项均满足才得到 `continue`。真实重放把这个判定写入 `outcome`；合成验收的 `outcome` 始终为 `pipeline_check_only`，计算出的门槛结果只保存在 `criterion_outcome`。即使真实重放输出 `continue`，仍不能外推为长程训练收益。

## 使用

在仓库根目录运行：

```powershell
python modes/codex-research-question/completion_order_bias_probe.py analyze INPUT.json --output RESULT.json
```

可先运行合成验收，检查上游导出器是否遵循契约：

```powershell
python modes/codex-research-question/completion_order_bias_probe.py demo `
  --input-output modes/codex-research-question/results/2026-09-05-completion-order-bias-probe-dry-run.input.json `
  --result-output modes/codex-research-question/results/2026-09-05-completion-order-bias-probe-dry-run.result.json
```

合成验收的 `scientific_status` 明确标为 `synthetic_pipeline_check_only`，只能证明数据契约、指标和门槛实现可执行。
