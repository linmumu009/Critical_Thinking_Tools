# 研究问题发现共享协议

日期：2026-08-21

状态：v2 冻结

## 核心定义

模式 1 和模式 2 是**同一条研究问题发现流水线的两个执行引擎**，不是两套方法：

- 模式 1：外部模型 API 执行；
- 模式 2：当前 Codex 执行。

两者必须使用相同输入范围、阶段顺序、思考工具、候选结构、硬门槛、评分卡、现实试探和最终问题契约。模式 2 可以利用 Codex 的本地文件、联网检索、持续上下文和产物保存能力，但不能因此增加、删除、合并或重排阶段。

## 唯一流程来源

业务流程以[问题发现漏斗](../../tools/question-discovery-funnel/README.md)为方法来源，以 [pipeline-stages.json](pipeline-stages.json) 为机器可检查的阶段定义。引擎适配提示词不能覆盖它们。

共享阶段为：

1. 阶段 0：确定研究使用者、研究决策、探索目标和停止条件；
2. 阶段 1：收集现实信号，严格分开观察、解释和未知；
3. 阶段 2：使用 Six Honest Serving Men 与苏格拉底式澄清完成 5W1H 和层级重构；
4. 阶段 3：先用 QFT 做生成—评价分离，再用 STORM/Co-STORM 式有来源多视角检索扩展问题空间；
5. 阶段 4：按回答依赖的机制和证据聚类去重，不按措辞相似度去重；
6. 阶段 5：使用双向钢人和竞争性假设分析写出合理答案 A/B/未知、行动映射与反转条件；
7. 阶段 6：先过评分卡五项硬门槛，再按六项 0–2 维度排序；
8. 阶段 7：对不超过三个入围问题执行廉价现实试探和最近工作碰撞，只能保留、缩小、改写或淘汰；
9. 阶段 8：交付一个主问题、两个备选和完整问题契约。

## 不可因模式改变的内容

以下内容属于流程，不属于引擎：

- 是否以及何时使用 QFT、STORM、苏格拉底追问、双向钢人和竞争性假设；
- 生成阶段与评价阶段的分离；
- 候选数量范围、机制/证据聚类和去重口径；
- 决策分叉、反转条件、硬门槛和评分维度；
- 最近工作碰撞、廉价现实试探与去留规则；
- 一个主问题、两个备选及问题契约的输出结构；
- 不要求用户给候选评分的运行规则。

任何模式专属流程变化都必须先修改共享流程并同时作用于两个引擎。

## 允许的引擎适配

模式 1 的 API 适配器可以处理无状态消息、模型配置、重试和结构化返回。模式 2 的 Codex 适配器可以连续读取本地材料、联网检索、保存阶段产物、运行校验和提供进度更新。

这些差异只能改变“怎样执行”，不能改变“执行什么”。模式 2 不读取或调用用户配置的外部模型 API；模式 1 的凭证和传输信息不得进入研究证据或结果。

适配提示词：

- [模式 1 API](prompts/mode-1-api.md)
- [模式 2 Codex](prompts/mode-2-codex.md)

## 运行与校验

创建会话时必须明确选择引擎：

```powershell
python modes/codex-research-question/research_question_session.py init --mode 1 --output <会话.json>
python modes/codex-research-question/research_question_session.py init --mode 2 --output <会话.json>
```

查看适配提示词：

```powershell
python modes/codex-research-question/research_question_session.py prompt --mode 1
python modes/codex-research-question/research_question_session.py prompt --mode 2
python modes/codex-research-question/research_question_session.py prompt --mode 2 --stage 3_expand
```

正式完成后：

```powershell
python modes/codex-research-question/research_question_session.py validate --session <会话.json> --complete
```

验证器会拒绝阶段顺序漂移、缺少规定工具、模式与引擎不匹配、未完成现实试探、未过硬门槛的入选问题，以及旧自主流程的 v1 会话。

## 历史结果边界

2026-08-21 生成的两份结果使用了后来新增的“证据扫描—六项硬门槛—实验合同”自主流程。它们可以继续作为研究候选材料，但没有执行本协议的共享阶段 0–8，因此不是正式模式 2 运行结果。
