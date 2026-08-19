# 条件 A：直接基线

你将看到一个决策案例。请先仅根据公开简报给出初始选择，然后通过提问改善决策。

规则：

- 最多提出 5 个问题，每次只问一个并等待 ORACLE 回答。
- 不得要求一次性提供“所有数据”“全部背景”或隐藏答案。
- 问题必须能由案例事实回答。
- 提问结束后，从给定选项中选择一个最终决策。

严格使用以下协议：

```text
PRE_DECISION: <option_id>
PRE_PROBABILITIES: {"option_a": <0到1>, "option_b": <0到1>, "option_c": <0到1>, "option_d": <0到1>}
QUESTION: <一个问题>
QUESTION: <下一个问题>
DECISION: <option_id>
PROBABILITIES: {"option_a": <0到1>, "option_b": <0到1>, "option_c": <0到1>, "option_d": <0到1>}
RATIONALE: <不超过 100 字>
```

两次概率均须包含全部选项且总和为 1。概率代表在当前信息下，每个选项成为最佳行动的主观概率。
