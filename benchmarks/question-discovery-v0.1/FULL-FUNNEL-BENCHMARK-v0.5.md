# 完整漏斗候选生成对照预注册 v0.5

日期：2026-08-25

状态：设计冻结，尚未运行

## 目的

验证完整问题发现漏斗在相同候选预算下，是否比原生模型和单个思考工具更容易提出能命中关键证据、区分反事实分支并改善后续决策的问题。

这项 benchmark 是组件级效度检查，不用于证明模式 1 或模式 2 在真实科研中的最终价值。正式模式还包含真实来源检索、逐阶段 checkpoint、最近工作碰撞和问题契约，本 benchmark 不具备这些条件。

## 条件

所有生成器看到相同公开案例和 `evidence_capabilities`，输出恰好 8 个候选，再交给同一个条件盲 matcher 去重并映射到相同隐藏证据目录。

| 条件 | 含义 |
| --- | --- |
| N | 原生模型直接处理案例，不经过候选菜单 |
| A | 普通“先提重要问题”对照 |
| G0 | 原生候选生成器 |
| GQ | 仅使用 QFT 的生成—评价分离与多角度发散 |
| GS | 仅使用 STORM 风格多视角 |
| GB | 仅使用双向钢人和关键变量 |
| GF | 压缩执行完整问题发现漏斗 |

GF 严格包含目标、现实信号、5W1H/苏格拉底重构、QFT、STORM、机制聚类、双向钢人/竞争假设、硬门槛、六维评分和 cheap-probe 重写，不增加候选数、证据目录或下游 selector 权限。

## 冻结比较

主要比较：

1. GF − G0：完整漏斗相对原生候选生成的增量；
2. GF − GQ：聚类、竞争答案和筛选是否超越纯发散；
3. GF − GS：完整决策分叉是否超越纯多视角；
4. GF − GB：先发散再收敛是否超越直接钢人化；
5. GF 与 N/A：候选生成—盲匹配—选择链的总体下游价值。

不得在看到结果后删除某个条件或只报告 GF 有利的案例。

## 设计

- 使用 `cases-v0.4/` 的 4 个反事实 pair、8 个隐藏 variant；
- 每个条件运行 3 个模型 seed；
- 每个 pair 的两个隐藏分支使用相同条件和 seed；
- 总计 `4 pairs × 7 conditions × 3 seeds = 84` 个成对单元，168 个模型会话；
- 运行顺序按预注册随机种子 20260901、20260902、20260903 打乱；
- matcher、selector、问题预算和 API/直接引擎配置在条件间固定；
- v0.4 四生成器的旧结果不并入 v0.5 主分析，避免 GF 是事后新增而其他条件复用旧样本。

生成计划：

```powershell
python benchmarks/question-discovery-v0.1/candidate_benchmark.py schedule `
  --seed 20260901 --repeats 3 `
  --output results/candidate-v0.5-plan.json
```

## 指标

首要指标：

- `both_branches_full_critical_coverage`；
- 下游 `counterfactual_separation_gain`；
- 两个隐藏分支最终都选对的比例。

次要指标：

- branch critical coverage；
- 去重后 evidence menu 大小；
- duplicate match count；
- correct-option probability gain；
- 模型调用数与实际问题数。

审计指标：

- 原子单观察率；
- evidence capability 可回答率；
- 候选间实质独立率；
- action-discriminating rate；
- matcher 分歧率与格式修复率。

## 推进门槛

GF 只有同时满足以下条件才可称为“优于原生候选生成”：

1. 相对 G0 的平均 branch critical coverage 为正；
2. `both_branches_full_critical_coverage` 不低于 G0；
3. 下游 counterfactual separation gain 的平均差为正；
4. 至少 3/4 pair 的方向不为负；
5. 原子性、可回答性和候选独立率均不低于 0.80；
6. 额外调用和 token 成本有完整报告。

如果只提高候选覆盖却不提高下游反事实分离，结论应是“问题菜单更广，但尚未证明决策价值”。如果效果只来自 matcher 偏好，应先修 matcher，不推广流程。

## 停止和修改规则

- 格式失败率超过 10%：暂停并只修输出协议，不改方法内容；
- matcher 与盲审不一致率超过 15%：暂停下游结论；
- 任一条件出现公开信息泄漏隐藏分支：该批次作废；
- GF 未过至少 4/6 推进门槛：不扩大案例；
- 任何提示修改都升级 benchmark 小版本并重新运行全部条件，不复用旧 GF 对照结果。

## 解释边界

合成反事实案例能测“是否问到预先定义的关键未知”，不能证明问题在真实科研中有新颖性。模式 2 是否真正更善于发现研究问题，还需要：

- 同一真实研究 brief 的模式 1/模式 2 多次独立运行；
- 冻结日期的一手来源碰撞；
- 条件盲的领域评审或后续 cheap probe 成功率；
- 候选稳定性、重复率和单位检索/计算成本。

该真实研究验证属于下一层，不得用本 benchmark 的合成案例结果替代。
