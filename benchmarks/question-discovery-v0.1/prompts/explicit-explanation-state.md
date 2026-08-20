# 条件 E（D2）：显式解释状态机

你将看到一个决策案例。请先仅根据公开简报给出初始选择，然后建立一张简洁、可检查的竞争解释地图，用尽可能少的问题识别最可能改变最佳行动的证据。

## 解释计划

初始选择之后，必须登记恰好 3 个竞争解释 H1、H2、H3。每个解释包含：

- `explanation`：对当前现象的一种具体机制解释；
- `evidence_target`：可观察且能支持或削弱该解释的证据目标；
- `action`：若该解释最受支持，应该成为最佳行动的 option_id。

三个解释必须指向三个不同的最佳行动，且不能只是同一指标的三种措辞。解释清单不是长篇推理，只用于后续状态控制。

## 提问状态机

每次提问必须同时输出一个尚未尝试的解释目标和一个问题：

1. `TARGET` 必须是 H1、H2、H3 中尚未尝试的一个。
2. `QUESTION` 必须直接检验该解释的 `evidence_target`，并具体到可观察的对象、分组、时间、流程、比较或指标。
3. 一个解释目标最多尝试一次。即使只改变问题措辞，也不得复用已经尝试的目标。
4. ORACLE 返回无事实后，该目标标记为 `NONE`；下一问必须切换到另一个尚未尝试的解释。
5. ORACLE 返回事实后，判断证据是否已经足够改变或稳固行动；足够则立即 `DECIDE`，否则只能尝试另一个尚未尝试的解释。
6. 三个解释目标都尝试后必须停止，不得为了用完 5 问预算继续提问。

目标不是收集最多信息，而是覆盖不同的决策机制，并把无效方向从搜索状态中明确排除。

## 协议

控制器会分阶段索取字段。严格使用以下格式：

```text
PRE_DECISION: <option_id>
PRE_PROBABILITIES: {"option_a": <0到1>, "option_b": <0到1>, "option_c": <0到1>, "option_d": <0到1>}
EXPLANATIONS: [{"id":"H1","explanation":"...","evidence_target":"...","action":"option_a"},{"id":"H2","explanation":"...","evidence_target":"...","action":"option_b"},{"id":"H3","explanation":"...","evidence_target":"...","action":"option_c"}]
TARGET: H1
QUESTION: <只检验 H1 证据目标的一个问题>
TARGET: H2
QUESTION: <只检验 H2 证据目标的一个问题>
DECISION: <option_id>
PROBABILITIES: {"option_a": <0到1>, "option_b": <0到1>, "option_c": <0到1>, "option_d": <0到1>}
RATIONALE: <不超过 100 字>
```

两次概率均须包含全部选项且总和为 1。概率代表在当前信息下，每个选项成为最佳行动的主观概率。

不得要求一次性提供所有数据、全部背景或隐藏答案；不得虚构简报未暗示的数据源；不得输出未被当前阶段要求的后续字段。
