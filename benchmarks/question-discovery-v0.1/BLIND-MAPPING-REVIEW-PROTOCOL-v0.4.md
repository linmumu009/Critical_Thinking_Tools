# Candidate Generation v0.4：条件盲人工映射复核协议

日期：2026-08-20

状态：评审前冻结；尚未录入人工评分

## 为什么先做这一步

Candidate Generation v0.4 的 48 份候选集已经完成自动答案盲映射，但匹配器与受测系统复用了同一模型配置，可能共享语义偏差。正式报告因此只把候选质量排序称为“自动映射主分析”，没有把它包装成人工确认结论。

本复核只回答一个决策问题：**自动匹配误差是否足以改变候选覆盖、生成器相对排序或预注册推进门槛？** 在回答之前不开发 GQ2，也不启动新的 API 大实验。

## 固定材料

- 来源只允许是三个正式完成进度账本中引用的 48 份候选工件；不得用文件名通配收集，以免混入限长前失败重跑留下的文件。
- 4 个案例 × 4 个生成条件 × 3 个模型种子，共 48 个候选集、384 道候选问题。
- 随机化种子固定为 `20260823`。
- 构建程序为 [blind_mapping_review.py](blind_mapping_review.py)。
- 已生成材料位于 [blind-review-v0.4/](blind-review-v0.4/)。

## 盲法

两位评审者只接收：

1. 优先使用完全离线的 `packet/review-form.html`，页面逐组显示材料、自动保存本机进度并导出标准 CSV；
2. 或使用 `packet/blind-review-packet.md` / 同内容 JSON 与各自独立的 `forms/reviewer-1.csv` 或 `forms/reviewer-2.csv`。

离线页面不加载任何外部脚本、字体、图片或接口，不包含解盲密钥，评分只保存在当前浏览器的本机存储中。两位评审者必须使用不同且固定的 `reviewer_id`。

实际分发优先使用 `reviewer-bundles/reviewer-1.zip` 和 `reviewer-bundles/reviewer-2.zip`。每份压缩包只含离线页面、盲包、对应空白表和评审者说明，不含 `coordinator/`；不要把整个仓库目录发送给评审者。

评审材料不得出现：

- G0/GQ/GS/GB 条件标签；
- 模型种子；
- 自动匹配结果和自动候选指标；
- 原始生成器或匹配器输出；
- 隐藏分支、事实值、criticality、正确行动或下游成绩；
- 正式工件文件名。

`coordinator/unblinding-key.json` 只供协调者在两份评分锁定后使用。创建案例的人不得担任唯一评审者；两位评审者在各自提交前不得讨论答案。

## 逐题评分

### 1. `mapped_evidence_id`

选择 `E1`～`E6` 中一个能够**单独且完整**回答候选问题的目录项。若需要组合多个目录项、目录只能回答一部分、候选要求目录没有的切分/交叉/因果量，或没有任何目录项可回答，填写 `NONE`。

不要选择“语义最接近”的目录项；标准是单项证据是否足以完整回答。

### 2. `atomic_single_observation`

- `1`：只要求一个观察、比较或反事实检验；
- `0`：同时要求多个可独立失败的观察、多个数据源拼接，或把问题、解释与行动建议合并在一起。

### 3. `fully_answerable_by_mapping`

- 映射 `E1`～`E6` 时必须为 `1`；
- 映射 `NONE` 时必须为 `0`。

这一冗余字段用于发现评审者把“部分匹配”误当作完整映射的录入错误。

### 4. `distinct_from_other_candidates`

- `1`：在同一八题候选集中检验不同观测或机制；
- `0`：与同组另一题在可获得证据和判别目标上实质重复，即使措辞或切分方式不同。

### 5. `action_discriminating`

- `1`：至少一种合理答案可能改变两个或更多公开行动的相对支持；
- `0`：答案只提供背景、描述、执行细节，或无论如何回答都不会改变行动排序。

所有质量字段只填 `0` 或 `1`。说明字段可选，但建议对 `NONE`、复合问题和边界案例简要说明。

## 独立评审与仲裁

1. 两位评审者独立完成全部 384 行。
2. 程序验证 packet hash、行数、候选 ID、合法映射、二元值以及映射—完整可答性一致性。
3. `prepare-adjudication` 只导出两人不一致的“候选 × 字段”行，并锁定两位原始值。
4. 仲裁者填写每个分歧的 `final_value` 和 `adjudicator_id`；不得删除低质量候选或修改候选文字。
5. 人工共识映射按原 C1～C8 顺序执行与正式实验相同的 `NONE` 移除和同证据去重，再重算候选指标。

## 评审可靠性门槛

映射字段同时报告完全一致率和 Cohen's kappa。若满足任一条件，不直接解释自动—人工敏感性，先修订规范并对随机 20% 候选重新做独立校准：

- 映射完全一致率 `< 0.85`；
- 映射 Cohen's kappa `< 0.70`。

kappa 是类别不平衡下的诊断量，不替代完全一致率。

## 实质差异与路线闸门

在评审可靠的前提下，满足任一条件即判为“匹配接口存在实质问题”，先修匹配器，再开发 GQ2：

1. 自动映射与仲裁后人工共识的不一致率 `≥ 0.10`；
2. 任一预注册候选推进门槛或条件最终 `passed` 判定发生翻转。

若不一致率 `< 0.10` 且没有门槛翻转，则认为 v0.4 的主要结论对映射误差稳定，主要瓶颈转向生成器，可进入 GQ2 开发。

下游主指标、准确率、案例护栏、调用量和完整性门槛不会因人工重映射而事后重跑或改写；敏感性分析只替换候选映射派生指标，再与冻结的下游结果合并判门槛。

## 固定输出

完成两份评审和仲裁后，分析程序生成：

- `sensitivity-results.json`：完整机器可读结果；
- `SENSITIVITY-REPORT.md`：自动值、人工值、评审一致性、门槛翻转和路线建议；
- `consensus-mappings.csv`：解盲后的逐题自动映射与人工共识。

结论只能是以下三类之一：

1. `repeat_rubric_calibration_before_interpreting_sensitivity`；
2. `fix_mapping_interface_before_gq2`；
3. `proceed_to_gq2_generator_development`。

## 命令

在本目录运行：

```powershell
python blind_mapping_review.py build
python blind_mapping_review.py validate-review blind-review-v0.4/forms/reviewer-1.csv
python blind_mapping_review.py validate-review blind-review-v0.4/forms/reviewer-2.csv
python blind_mapping_review.py prepare-adjudication `
  --reviewer-one blind-review-v0.4/forms/reviewer-1.csv `
  --reviewer-two blind-review-v0.4/forms/reviewer-2.csv `
  --output blind-review-v0.4/coordinator/adjudication.csv
python blind_mapping_review.py analyze `
  --reviewer-one blind-review-v0.4/forms/reviewer-1.csv `
  --reviewer-two blind-review-v0.4/forms/reviewer-2.csv `
  --adjudication blind-review-v0.4/coordinator/adjudication.csv `
  --output blind-review-v0.4/analysis
```

`build` 拒绝覆盖非空目录，避免抹掉已经开始的人工评分。当前两份 CSV 是有 384 行的空白锁定模板；在填写前运行 `validate-review` 应失败，这是预期行为。
