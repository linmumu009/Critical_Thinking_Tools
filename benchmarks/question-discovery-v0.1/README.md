# Question Discovery Benchmark v0.2

这是“问题发现漏斗”的首个最小可行基准，用于比较三种提问条件是否能在固定问题预算内改善决策。

首次 9 次 API 小批次的结果与停止规则见 [PILOT-01.md](PILOT-01.md)：运行链路可用，但案例出现严重决策天花板，剩余 99 次在修订案例前暂停。

v0.2 已针对试点发现完成修订：公开选项改为中性 ID、最佳选项位置均衡，并新增提问前后的完整概率分布及 Brier 质量改善指标。详细定义见 [METRICS-v0.2.md](METRICS-v0.2.md)。目录名暂时保留 `question-discovery-v0.1`，以避免破坏已有本地会话和外部链接；会话内的 `benchmark_version` 才是权威版本。

v0.2 校准确认仍有严重的提问前选择天花板。v0.3 已新增 4 组反事实成对案例和独立预检入口，设计与命令见 [PAIRED-BENCHMARK-v0.3.md](PAIRED-BENCHMARK-v0.3.md)，首次 API 结果见 [PREFLIGHT-REPORT-v0.3.md](PREFLIGHT-REPORT-v0.3.md)。

完整成对比较现包含 N 原生模型、A 普通提问、B 工具串联、C 问题发现漏斗和 D 最小判别问题五个条件。主指标是提问后反事实分支概率分离增量，定义见 [METRICS-v0.3.md](METRICS-v0.3.md)。

首个产品案例四条件成对烟雾结果见 [SMOKE-REPORT-v0.3-product.md](SMOKE-REPORT-v0.3-product.md)。

跨案例长批次使用 `paired_benchmark.py calibrate`；它会在每个成对单元后保存进度并支持安全续跑，详细参数见 [PAIRED-BENCHMARK-v0.3.md](PAIRED-BENCHMARK-v0.3.md)。

首轮包含 N 原生模型的四条件完整校准已经结束，汇总、验证与结论边界见 [CALIBRATION-REPORT-v0.3-seed1.md](CALIBRATION-REPORT-v0.3-seed1.md)。

N/A 三种子确认显示普通提问的总体优势很小且存在稳定案例异质性，完整报告见 [CONFIRMATION-REPORT-v0.3-seeds1-3.md](CONFIRMATION-REPORT-v0.3-seeds1-3.md)。据此新增 D“最小判别问题”，冻结提示、开发门槛和排除规则见 [D-PRE-REGISTRATION-v0.3.md](D-PRE-REGISTRATION-v0.3.md)。

D 三种子开发结果见 [D-DEVELOPMENT-REPORT-v0.3.md](D-DEVELOPMENT-REPORT-v0.3.md)：主指标 `+0.488`，介于 N 与 A 之间，只通过 5 项推进门槛中的 3 项，不原样进入新案例。主要失败是收到无事实反馈后仍沿同一问题簇继续细化；下一版应改用显式竞争解释状态和硬性换轨控制。

## 当前范围

- 12 个合成但内部可判定的决策案例。
- 产品、运营、研究、项目四个领域，每类 3 个。
- 每例包含公开简报、隐藏事实、关键未知、行动选项和预先定义的效用。
- 公开选项只使用 `option_a`～`option_d`，不暴露内部机制型标识；最佳选项位置在全体案例中均衡。
- 每次最多提出 5 个问题。
- A/B/C 三种条件共需 `12 × 3 × 3 = 108` 次运行（每条件 3 个随机种子）。

这些案例由项目维护者构造，不应被称为“专家金标准”。它们适合验证流程和发现明显差异；正式结论仍需要领域专家案例和真实使用者实验。

## v0.2 三种条件

| 条件 | 文件 | 含义 |
| --- | --- | --- |
| A | [baseline.md](prompts/baseline.md) | 直接要求模型提出最重要的问题 |
| B | [tool-chain.md](prompts/tool-chain.md) | 使用既有 QFT/STORM/钢人工具串联 |
| C | [discovery-funnel.md](prompts/discovery-funnel.md) | 使用问题发现漏斗 |

三组使用相同案例、相同事实 oracle、相同 5 问预算和相同最终决策选项。

## 快速开始

在本目录运行：

```powershell
python benchmark.py validate
python benchmark.py list
python benchmark.py show product-01
python benchmark.py schedule --output results/run-plan.json
python benchmark.py calibration-schedule --output results/calibration-v0.2.json
python benchmark.py run product-01 --condition A
```

`schedule` 使用固定随机种子生成 108 次盲测的随机执行顺序；可用 `--seed` 改变顺序并保留复现参数。运行计划、会话和结果默认不提交到 Git，以免把未审查输出混入基准定义。

`calibration-schedule` 生成 v0.2 的 12 次校准集：每个案例运行一次，并保证每个领域内 A/B/C 各出现一次。本轮已经完成，但因 12/12 次提问前选择最佳行动而未通过天花板门槛；详见 [v0.2 校准报告](CALIBRATION-REPORT-v0.2.md)。在反事实成对案例通过预检前，不恢复 108 次正式矩阵。

`run` 每次开始都会要求选择一种模式：

1. **API 自动运行**：程序逐轮调用模型、向模型返回 Oracle 答案并保存结果。
2. **Codex 直接处理**：把公开案例交给当前 Codex 对话，由 Codex 逐题作答，终端负责返回 Oracle 答案和记录结果。

也可在自动化场景显式指定 `--mode api` 或 `--mode direct`，但正式人工启动时建议保留选择步骤。

## API 配置

编辑本目录中的 `model-config.local.json`，只需填写前三项：

```json
{
  "url": "https://your-provider.example/v1",
  "api_key": "YOUR_API_KEY",
  "model_name": "YOUR_MODEL_NAME",
  "oracle_mode": "semantic_api",
  "oracle_url": "",
  "oracle_api_key": "",
  "oracle_model_name": ""
}
```

- `url` 可以是 OpenAI 兼容服务的基础 URL（程序会补上 `/chat/completions`），也可以是完整接口地址。
- `model-config.local.json` 已被 Git 忽略，不会随正常提交上传；仓库只保存不含真实凭证的 `model-config.example.json`。
- 可先运行 `python benchmark.py check-config` 检查必填项；这个命令不会发起网络请求，也不会显示密钥。
- 可选参数 `timeout_seconds`、`api_max_retries`、`temperature` 和 `send_seed` 已提供默认值。网络超时、HTTP 429 或 5xx 默认重试一次；若服务不接受 `seed` 参数，将 `send_seed` 改为 `false`。
- `semantic_api` 会在与受测对话隔离的请求中，从尚未揭示的固定事实里只选择一个 `fact_id`；答案仍由本地事实表返回，受测对话看不到事实表。
- Oracle 三项留空时会复用同一 API 和模型，适合校准但不满足正式独立性要求。正式实验应填写独立的 Oracle 端点/密钥/模型，或逐题人工复核。
- 若只想复现旧的关键词行为，可将 `oracle_mode` 设为 `keyword`。语义模式每个问题会增加一次 API 调用。

`run` 会：

1. 展示对应条件提示词和公开案例。
2. 要求记录模型在提问前的初始决策。
3. 接收最多 5 个模型问题，并由隐藏事实 oracle 返回一条最匹配事实。
4. 记录提问前后的选项概率分布与最终决策。
5. 将不含完整隐藏事实表的会话写入本地 `sessions/`。
6. 计算决策改善、关键未知命中和单位问题信息效率。

模型输出协议见各条件提示。API 模式可能产生服务商费用；直接模式不调用外部模型 API。

## 防泄漏规则

- 不要把 `cases/*.json` 直接提供给受测模型；它们包含隐藏事实和正确决策。
- 只能使用 `benchmark.py show` 或 `benchmark.py run` 输出的公开简报。
- 创建案例的人不应担任唯一盲评者。
- 同一模型运行不同条件时使用全新对话，清除缓存与先前案例内容。
- 条件标签和输出顺序在人工评分前应随机化。
- 不要把 `model-config.local.json`、终端输出或任何包含密钥的内容提交到仓库。

## 自动指标

- `pre_utility` / `post_utility`：提问前后选项效用。
- `decision_improvement`：`post_utility - pre_utility`。
- `normalized_post_utility`：最终效用除以该案例最高效用。
- `key_unknown_recall`：已揭示关键事实权重占全部关键事实权重。
- `critical_fact_hit_rate`：已揭示关键事实数占全部关键事实数。
- `information_efficiency`：决策改善除以实际问题数。
- `pre_probability_quality` / `post_probability_quality`：基于完整选项概率的归一化多分类 Brier 质量。
- `probability_quality_improvement`：提问后的概率质量减去提问前质量，是 v0.2 首要指标。
- `best_option_probability_change`：最佳选项概率变化。
- `probability_information_efficiency`：每个问题带来的概率质量改善。
- `no_fact_answer_rate`、`protocol_deviation_count`：自动护栏。
- `oracle_match_disagreement_rate`：语义 Oracle 与关键词原型选择不同事实的比例，用于审计关键词脆弱性。

## 需要盲评的指标

以下指标不能由字符串匹配可靠判断，保留在会话记录中供盲评：

- 决策改变问题率；
- 判别性问题率；
- 虚构前提率；
- 不可回答问题率；
- 虚假平衡与敏感信息风险；
- 问题清晰度和真实使用价值。

## 已知限制

- 当前 oracle 使用关键词匹配，不具备完整语义理解；若合理问题没有命中，应记录为 oracle 错误，而不是把责任算给模型。
- 正式批量运行前必须先做校准调用；若合理问题与事实匹配异常，先按固定事实表人工审计。不得继续用逐句追加关键词掩盖开放语言匹配问题。
- API 控制器会记录可恢复的格式偏离；例如模型用 `DECISION` 而非 `DECIDE` 提前结束时，会继续收集最终理由，并将偏离写入 `protocol_deviations`。
- 初始或最终概率格式无效时，控制器允许一次只修正格式的重试；重试不提供新案例事实，原输出与错误原因会记录为协议偏离，第二次仍无效则停止。
- 合成案例的效用函数比真实世界明确，可能高估“决策分叉”方法的优势。
- 12 个案例不足以支持稳定的模型排名。
- 自动指标不能替代盲评和真实用户结果。

校准问题与修订记录见 [CALIBRATION.md](CALIBRATION.md)，完整汇总结论见 [CALIBRATION-REPORT-v0.2.md](CALIBRATION-REPORT-v0.2.md)。
