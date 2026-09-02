# 可恢复无人化 Runner 与证据账本

版本：v1.2（2026-09-02）

`research_question_runner.py` 是模式 1、模式 2 共用的运行控制层。它不改变阶段 0–8，也不代替任何思考工具；它负责创建运行、生成当前阶段包、原子保存 checkpoint、失败记录、断点恢复、证据检索账本、语义审计和最终交付清单。

## 无人化边界

- 模式 2 由当前 Codex 执行阶段内容。Runner 不启动第二个 Codex 进程，也不读取或调用外部模型 API。
- 用户选定模式并授权开始后，Codex 应连续调用 Runner、完成下一阶段、保存 checkpoint 并自动恢复，不要求用户给候选评分或逐阶段确认。
- 缺少会实质改变研究方向的真实约束、外部权限或数据时，仍按共享协议暂停；Runner 不能伪造缺失证据。
- 模式 1 使用相同 Runner 和 envelope，只由外部 API 引擎填写阶段产物。

## 一次运行

初始化：

```powershell
python modes/codex-research-question/research_question_runner.py init `
  --mode 2 `
  --run-id 2026-08-25-example
```

取得当前阶段包：

```powershell
python modes/codex-research-question/research_question_runner.py next `
  --run 2026-08-25-example
```

阶段包包含引擎适配提示、唯一共享阶段契约、已完成 checkpoint 和 envelope 输出字段。Codex 完成阶段后写一个 JSON envelope：

```json
{
  "schema_version": "1.0",
  "run_id": "2026-08-25-example",
  "stage_id": "0_goal",
  "output_summary": "冻结研究决策、期限与停止条件。",
  "artifact_refs": ["stage://0_goal"],
  "payload": {
    "goal": "阶段完整产物保存在这里；Runner 不限制其内部表达。"
  },
  "session_updates": {
    "input_manifest": [],
    "decision_log": ["阶段 0：目标已冻结。"]
  }
}
```

保存并进入下一阶段：

```powershell
python modes/codex-research-question/research_question_runner.py checkpoint `
  --run 2026-08-25-example `
  --envelope <阶段-envelope.json>
```

如果执行失败，记录失败但不推进阶段：

```powershell
python modes/codex-research-question/research_question_runner.py fail `
  --run 2026-08-25-example `
  --message "检索服务暂时不可用"
```

再次调用 `next` 会从第一个未完成阶段恢复。Runner 拒绝跳阶段、跨 run envelope、重复 input/evidence ID 和阶段越权修改。

新运行生成 schema v2.1。阶段 4 必须保留“核心问题—机制—边界—干预”问题树，阶段 6 必须应用九项硬门槛，阶段 8 可以合法交付 `no_better_question`，不再强迫每轮选择一个更狭窄的新问题。

## 可复现证据账本

每个检索式都必须记录：

```powershell
python modes/codex-research-question/research_question_runner.py log-query `
  --run 2026-08-25-example --id Q01 `
  --text "检索式" --provider web `
  --scope "近 18 个月一手论文" --purpose exact-question `
  --result-count 12
```

每项纳入或排除决定必须关联检索式，并写明它支持什么、为何纳入或排除：

```powershell
python modes/codex-research-question/research_question_runner.py log-source `
  --run 2026-08-25-example --evidence-id E01 --query-id Q01 `
  --disposition include --location https://example.org/paper `
  --source-type primary_paper --claim "支持的具体观察" `
  --reason "纳入/排除理由"
```

阶段 7 的主问题和两个备选都必须有先行研究 collision review。每个候选至少关联三条用途分别为 `exact-question`、`mechanism`、`adjacent-terminology` 的检索式：

```powershell
python modes/codex-research-question/research_question_runner.py log-collision `
  --run 2026-08-25-example --candidate-id C01 `
  --query-id Q01 --query-id Q02 --query-id Q03 `
  --closest-evidence-id E01 --overlap "与最近工作的重叠" `
  --increment "仍未被覆盖的机制、边界或判别实验" `
  --prior-art-verdict incremental `
  --disposition keep
```

还必须记录常识性/非平凡性审查。`obvious-baseline` 是无需新实验即可预期的答案，`residual-uncertainty` 必须指出真正未知的效应大小、边界、机制或反常条件：

```powershell
python modes/codex-research-question/research_question_runner.py log-common-knowledge `
  --run 2026-08-25-example --candidate-id C01 `
  --basis-evidence-id E01 `
  --obvious-baseline "增加高质量数据通常会改善目标任务表现" `
  --residual-uncertainty "在等 token、跨域评测下哪种选择机制产生增益" `
  --counterexample-or-boundary "高质量代理分数可能筛掉困难但有用的样本" `
  --verdict context-dependent --disposition keep
```

最终入选只接受 `nontrivial` 或 `context-dependent` 的常识审查，以及 `no-direct-match-found` 或有明确增量的 `incremental` 先行研究结论。`common-knowledge`、`covered` 或 `uncertain` 必须淘汰、缩小或改写后重查。完整运行若缺少搜索记录、在线证据决定、三类先行检索或任一入选问题的两项审查，将不能 finalize。

## 两层校验

结构校验检查阶段、工具、schema、证据引用、硬门槛、评分和最终契约：

```powershell
python modes/codex-research-question/research_question_session.py validate `
  --session <会话.json> --complete
```

语义审计继续检查：

- 最终契约是否保持主候选的研究对象、比较、结果、范围和次级问题树；白话改写不再用字符串相似度判断；
- 契约触发信号是否来自主候选；
- A/B 是否真的映射到不同动作；
- 主问题若不是最高分，是否产生显式警告；
- 候选是否高度近重复；
- 是否出现所有候选全过硬门槛或全部同分的饱和现象；
- 证据地址是否重复、核查日期是否有效。

```powershell
python modes/codex-research-question/research_question_session.py audit `
  --session <会话.json>
```

Runner 的 `audit --complete` 同时执行结构、语义和证据账本审计。最终交付：

```powershell
python modes/codex-research-question/research_question_runner.py finalize `
  --run 2026-08-25-example `
  --result modes/codex-research-question/results/<正式结果>.json
```

它会输出正式 session、证据账本、run manifest 和所有文件的 SHA-256。正式可读报告仍由阶段 8 生成并放入 `results/`。

## benchmark 边界

候选生成 benchmark v0.5 新增 `GF` 完整漏斗条件，与原生生成器 `G0`、QFT `GQ`、STORM `GS` 和双向钢人 `GB` 使用相同 8 个问题预算、同一盲匹配器和相同反事实案例。它只测试压缩提示的候选覆盖与下游决策价值，不等同于带真实检索和阶段 checkpoint 的正式模式运行。

历史 v0.4 的四生成器结果和盲审保持冻结；新增 GF 不会进入旧审计。
