# 条件 F：竞争解释证据契约

你将看到一个决策案例，以及一份不含答案值的 `evidence_catalog`。目录中的每一项都是当前证据环境能够回答的一个原子问题；两个隐藏分支看到完全相同的目录，其中也可能包含与最佳行动无关的项目。

请先建立三个竞争解释，把每个解释绑定到一个目录证据和一个可能的最佳行动，再按决策价值选择验证顺序。你不能自由扩写复合数据需求；控制器只执行解释预先绑定的目录问题。

## 解释计划

初始选择后登记恰好 3 个解释 H1、H2、H3。每项包含：

- `explanation`：一种具体机制；
- `evidence_id`：能支持或削弱该解释的目录 id；
- `action`：若该解释最受支持，应成为最佳行动的 option_id。

三个解释必须绑定三个不同的 `evidence_id`，并指向三个不同的行动。目录中未绑定的项目保留为未采用的候选或干扰项。

## 状态规则

1. 每轮只选择一个尚未尝试的 `TARGET`；控制器执行该解释绑定的目录问题。
2. 一个 TARGET 和一个 `evidence_id` 都最多尝试一次。
3. 返回事实后，判断是否足以改变或稳固行动；足够则立即 `DECIDE`。
4. 若仍不足，只能切换到另一个尚未尝试的解释。
5. 三个解释全部尝试后必须停止。
6. 不得自行提出目录外问题、改写目录问题或猜测未返回的答案。

## 协议

```text
PRE_DECISION: <option_id>
PRE_PROBABILITIES: {"option_a": <0到1>, "option_b": <0到1>, "option_c": <0到1>, "option_d": <0到1>}
EXPLANATIONS: [{"id":"H1","explanation":"...","evidence_id":"E1","action":"option_a"},{"id":"H2","explanation":"...","evidence_id":"E2","action":"option_b"},{"id":"H3","explanation":"...","evidence_id":"E3","action":"option_c"}]
TARGET: H1
TARGET: H2
DECISION: <option_id>
PROBABILITIES: {"option_a": <0到1>, "option_b": <0到1>, "option_c": <0到1>, "option_d": <0到1>}
RATIONALE: <不超过 100 字>
```

两次概率均须包含全部选项且总和为 1。不得输出当前阶段未要求的后续字段。
