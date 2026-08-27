# 研究问题发现：同一流程，两种引擎

本目录名保留 `codex-research-question` 以避免破坏已有链接；从 v2 起，它承载的是模式 1 和模式 2 共用的研究问题发现流程。

## 两种模式到底差在哪里

| 项目 | 模式 1 | 模式 2 |
| --- | --- | --- |
| 执行引擎 | 外部模型 API | 当前 Codex |
| 适配提示词 | 面向无状态 API 调用 | 面向 Codex 的本地、联网和持续执行能力 |
| 流程阶段 | 与模式 2 完全相同 | 与模式 1 完全相同 |
| 思考工具 | 与模式 2 完全相同 | 与模式 1 完全相同 |
| 门槛、评分和输出 | 与模式 2 完全相同 | 与模式 1 完全相同 |
| 用户候选评分 | 不要求 | 不要求 |

一句话：**模式 2 只换发动机和提示词适配，不另造流程。**

## 共用流程

两种模式都执行原有[问题发现漏斗](../../tools/question-discovery-funnel/)：

```text
目标
→ 现实信号
→ 5W1H/苏格拉底式重构
→ QFT 发散
→ STORM 有来源多视角扩展
→ 按机制与证据聚类去重
→ 双向钢人/竞争假设形成决策分叉
→ 硬门槛与信息价值排序
→ 常识性审查/三路先行研究检索/廉价现实试探
→ 问题契约
```

详细要求见：

- [共享协议](PROTOCOL.md)
- [机器可检查的阶段定义](pipeline-stages.json)
- [研究领域画像与两种引擎](research-profile.json)
- [会话产物规范](SESSION-SCHEMA.md)
- [模式 1 API 适配提示词](prompts/mode-1-api.md)
- [模式 2 Codex 适配提示词](prompts/mode-2-codex.md)

## 无人化运行

用户选定模式以后，当前 Codex 可以使用共享 [Runner](RUNNER.md) 连续执行，无需用户逐阶段确认或给候选评分：

```powershell
python modes/codex-research-question/research_question_runner.py init --mode 2 --run-id <运行名>
python modes/codex-research-question/research_question_runner.py next --run <运行名>
python modes/codex-research-question/research_question_runner.py status --run <运行名>
```

每阶段原子保存，失败不会推进阶段，再次调用 `next` 自动从第一个未完成阶段恢复。完成前必须同时通过结构校验、语义审计和可复现证据账本审计。Runner 只做控制与审计，不改变模式 1/2 的共享方法；模式 2 仍不会读取或调用外部 API。

## 使用方式

开始一轮研究问题发现时选择：

1. 模式 1：外部模型 API；
2. 模式 2：当前 Codex。

随后两种模式都从阶段 0 开始，不能直接跳到文献扫描、候选评分或实验设计。模式 2 不读取外部模型 API 凭证，也不调用用户配置的模型服务。

## 历史说明

v0.27.0–v0.29.0 曾把模式 2 错误实现为一套独立的 Codex 自主研究流程。对应报告仍保存在 [results/](results/) 供参考，但已标记为历史非正式结果，不用于证明模式 2 已被完整执行。
