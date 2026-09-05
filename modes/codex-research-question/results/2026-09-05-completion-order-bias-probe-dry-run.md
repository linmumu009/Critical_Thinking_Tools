# 完成顺序偏差探针：合成验收

日期：2026-09-05

## 结论

完成顺序偏差的分析探针已经可运行，并通过固定轨迹集合、四条件完整性、梯度维度、三随机种子和三档剂量反应检查。当前没有真实异步 RL rollout 缓存，因此本次状态是 **`pipeline_check_only`**，不提供“完成顺序偏差存在或不存在”的模型证据。

## 合成验收结果

| 检查 | 结果 |
| --- | --- |
| 轨迹 | 12 条合成轨迹，2 个行为版本 |
| 条件 | 真实顺序、版本内随机、版本平衡、版本同步 oracle |
| 重复 | 3 个种子 × 3 档时延相关性 × 4 个更新步 |
| 固定集合审计 | 通过 |
| 主注入水平平均余弦差 | `0.00721`，低于预注册门槛 `0.05` |
| 跨种子方向 | 3/3 同方向 |
| 剂量反应 | Spearman `1.0` |
| 门槛计算 | `stop_or_narrow` |
| 科学结论 | 不适用；最终输出强制为 `pipeline_check_only` |

合成参数故意让主效应低于门槛，用于确认系统不会因为方向一致和剂量反应存在，就越过最小效应量要求。

## 真实数据接口

[AReaL 轨迹导出文档](https://github.com/areal-project/AReaL/blob/main/docs/en/reference/rollout_workflow.md)显示，开启轨迹导出后可以取得 `head_version`、`tail_version`、奖励、prompt 和 completion，并按训练版本保存 JSONL；其 [staleness manager](https://github.com/areal-project/AReaL/blob/main/areal/infra/staleness_manager.py) 也显式跟踪 rollout 的提交、运行、接受和拒绝状态。现有公开导出说明没有列出本探针仍需的 wall-clock 完成时间、逐步梯度向量和逐轨迹有效权重，因此不能直接从公开 dump 得出主指标。

真实重放需要为每条轨迹补充：

1. 稳定轨迹 ID、prompt ID、行为版本、提交时间、完成时间和持续时间。
2. 四个反事实条件在每一步产生的梯度向量。
3. 每一步各轨迹进入 loss 后的有效权重。
4. 256–512 条固定轨迹、至少两个行为版本、三个随机种子和至少三档时延相关性。

## 下一步

优先在 AReaL 或同等异步训练实现中增加时间戳、梯度摘要和有效权重导出，再把结果转换成 [探针输入契约](../COMPLETION-ORDER-PROBE.md)。真实数据到位前，不启动完整训练，也不把本次合成门槛计算写成研究发现。

## 可复现文件

- [分析结果](2026-09-05-completion-order-bias-probe-dry-run.result.json)
- [分析器与合成生成器](../completion_order_bias_probe.py)
- [输入与判定契约](../COMPLETION-ORDER-PROBE.md)
