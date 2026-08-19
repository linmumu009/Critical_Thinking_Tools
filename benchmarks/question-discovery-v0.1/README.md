# Question Discovery Benchmark v0.1

这是“问题发现漏斗”的首个最小可行基准，用于比较三种提问条件是否能在固定问题预算内改善决策。

## 当前范围

- 12 个合成但内部可判定的决策案例。
- 产品、运营、研究、项目四个领域，每类 3 个。
- 每例包含公开简报、隐藏事实、关键未知、行动选项和预先定义的效用。
- 每次最多提出 5 个问题。
- A/B/C 三种条件共需 `12 × 3 × 3 = 108` 次运行（每条件 3 个随机种子）。

这些案例由项目维护者构造，不应被称为“专家金标准”。它们适合验证流程和发现明显差异；正式结论仍需要领域专家案例和真实使用者实验。

## 三种条件

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
python benchmark.py run product-01 --condition A
```

`schedule` 使用固定随机种子生成 108 次盲测的随机执行顺序；可用 `--seed` 改变顺序并保留复现参数。运行计划、会话和结果默认不提交到 Git，以免把未审查输出混入基准定义。

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
  "model_name": "YOUR_MODEL_NAME"
}
```

- `url` 可以是 OpenAI 兼容服务的基础 URL（程序会补上 `/chat/completions`），也可以是完整接口地址。
- `model-config.local.json` 已被 Git 忽略，不会随正常提交上传；仓库只保存不含真实凭证的 `model-config.example.json`。
- 可先运行 `python benchmark.py check-config` 检查必填项；这个命令不会发起网络请求，也不会显示密钥。
- 可选参数 `timeout_seconds`、`temperature` 和 `send_seed` 已提供默认值。若服务不接受 `seed` 参数，将 `send_seed` 改为 `false`。

`run` 会：

1. 展示对应条件提示词和公开案例。
2. 要求记录模型在提问前的初始决策。
3. 接收最多 5 个模型问题，并由隐藏事实 oracle 返回一条最匹配事实。
4. 记录模型最终决策。
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

## 需要盲评的指标

以下指标不能由字符串匹配可靠判断，保留在会话记录中供盲评：

- 决策改变问题率；
- 判别性问题率；
- 虚构前提率；
- 不可回答问题率；
- 虚假平衡与敏感信息风险；
- 问题清晰度和真实使用价值。

## 已知限制

- v0.1 oracle 使用关键词匹配，不具备完整语义理解；若合理问题没有命中，应记录为 oracle 错误，而不是把责任算给模型。
- 正式批量运行前必须先做校准调用；若校准发现合理问题未命中，先扩充触发词、添加回归测试，并废弃该次校准结果。
- 合成案例的效用函数比真实世界明确，可能高估“决策分叉”方法的优势。
- 12 个案例不足以支持稳定的模型排名。
- 自动指标不能替代盲评和真实用户结果。

校准问题与修订记录见 [CALIBRATION.md](CALIBRATION.md)。
