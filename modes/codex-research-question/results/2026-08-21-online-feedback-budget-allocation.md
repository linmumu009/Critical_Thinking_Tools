# 历史候选报告（非正式模式 2）：在线反馈算力分配

> 身份更正：本报告使用的是已撤销的 Codex 自主六阶段流程，没有执行与模式 1 相同的共享问题发现漏斗。内容可作为候选材料参考，但不能声称已经过正式模式 2。

日期：2026 年 8 月 21 日

范围：LLM 文本后训练、GRPO/RLVR、学习型验证器、合成与自验证数据

本地输入：[LLM_posttraining_research_questions_2025_2026.xlsx](../../../project/GRPO-X1/LLM_posttraining_research_questions_2025_2026.xlsx)

## 结论先行

本轮最值得做的问题是：

> **在固定总反馈算力的 GRPO/在线 RLHF 中，能否根据组内的 policy-sampling uncertainty、同一验证器复评方差与跨验证器错误相关性，在线选择“生成新 rollout、复评已有响应或调用异质验证器”，从而比任一静态分配策略更少产生 advantage 符号错误并提高隐藏真值任务收益？**

我认为它有价值，不是因为“预算分配”没人做，而恰恰是因为相关组件已经分别成熟，却还没有被放到同一个训练时决策里：

- SARA/VIP 已在回答“还要不要为这个 prompt 生成更多 rollout”；
- uncertainty routing 已在回答“要不要把这个 response pair 交给强 judge”；
- repeated verification 已显示“复评同一响应”能降随机方差，但不能消除相关偏差；
- cross-model jury 已显示“换一个家族的模型”能通过误差去相关带来不同价值；
- RACE 已经覆盖宏观的 search / learning / feedback FLOP 分配，因此宽泛问题已经不新。

**尚未被回答的是**：在同一个 GRPO 组内，下一单位算力应该买一个新响应、买一次重复判断，还是买一个不同错误结构的判断？哪个动作最能减少真正改变梯度方向的 `advantage sign error`？

## 我从工作簿里看到的研究地形

工作簿包含 5,608 条高召回记录、940 条核心论文、240 条 2026 arXiv 记录和 42 条已整理研究空白。已有选题已经密集覆盖以下区域：

- 动态 group size、batch composition、归一化与 reward noise；
- verifier calibration、drift、abstention、conflict 与 reward hacking；
- teacher/student 家族、合成数据血缘、闭环同源偏差；
- pass@k、多样性坍缩、课程学习、异步陈旧性与复现性。

因此，“动态 rollout 数”“验证器不确定性路由”“同源验证偏差”“离线 F1 不代表在线收益”本身都不够新。新的窗口位于这些空白的交叉处：**将 policy sampling error、verifier variance 和 cross-verifier correlated bias 分开估计，并让三种计算动作在同一预算下竞争。**

## 主问题为什么可能形成强论文

### 1. 它有明确的机制分解

三类动作分别针对不同误差：

| 计算动作 | 主要减少的误差 | 什么时候最值钱 |
| --- | --- | --- |
| 生成新 rollout | policy sampling error、组内候选覆盖不足 | 当前组可能饱和、候选太少、rewardable support 尚不明确 |
| 复评已有响应 | 同一 verifier 的随机解码/提示敏感方差 | 候选排序接近、复评方差高但偏差相对低 |
| 调用异质 verifier | 模型特有的相关偏差与方向性 FPR/FNR | 同一 verifier 反复判断仍稳定但可能稳定地错 |

这给出了可检验的策略结构：如果分解成立，最优动作应随组状态改变；如果最终所有组都选择近似相同的比例，联合在线分配的机制就失败。

### 2. 它有比最终分数更靠近因果链的主指标

只看最终 benchmark score 容易被训练方差掩盖。主指标应是：

1. 相对隐藏精确 oracle 的 group-wise advantage 符号错误率；
2. 估计 advantage 与 oracle advantage 的排序一致性；
3. 估计 policy gradient 与 oracle policy gradient 的余弦相似度；
4. 最后才是等 FLOP 下的 held-out oracle pass@1、best@k 和学习曲线面积。

这样即使短训练没有显著终局增益，也能判断失败来自误差分解不成立、acquisition rule 不对，还是梯度改善没有转化为策略收益。

### 3. 最小实验可以先不用昂贵人评

用有精确答案或隐藏单元测试的数学、代码任务作为 oracle-hidden 环境：训练算法只能看到三个学习型 LLM judge 的奖励，精确 oracle 只用于评估。这样既能模拟开放任务的 noisy feedback，又保留干净的反事实真值。

建议的最小闭环：

- 一个 1.5B–3B policy；
- 三个不同模型家族的 7B–14B judge；
- 约 2,000 个训练 prompt、500–1,000 个测试 prompt；
- 每组最多 8 个 rollout；
- 50–100 个 GRPO 更新、至少 3 个种子；
- 4 张 A100 80GB 或等效算力，预计 3–7 GPU-days；
- 所有方法按实测生成 token 与 verifier inference FLOPs 等成本比较。

### 4. 强基线已经清楚

- uniform 固定 rollout + 单 judge；
- SARA 或 VIP 的 rollout-only allocator；
- uncertainty-to-strong-judge router；
- fixed-repeat；
- fixed heterogeneous jury；
- RACE pilot grid 选出的最佳静态混合；
- joint online allocator。

这避免了只和弱基线比较。真正需要赢的是开发集上调好的最佳静态混合，而不是 uniform。

## 新颖性碰撞：为什么它没有被最近论文直接做掉

| 最近工作 | 已经解决 | 仍未解决 |
| --- | --- | --- |
| [SARA](https://arxiv.org/abs/2607.26253)、[VIP](https://arxiv.org/abs/2602.01601) | 为 prompt 自适应分配新 rollout | 把 reward 当可靠观测；不比较复评或异质 verifier |
| [RACE](https://arxiv.org/abs/2607.13389) | 宏观分解 search、learning、feedback FLOPs | 固定或网格式训练配方；不做 group-level 下一动作选择 |
| [Ask a Strong LLM Judge](https://arxiv.org/abs/2510.20369) | 不确定 response pair 路由到强 judge | 不让新 rollout、复评和异质 panel 同时竞争预算 |
| [LLM-as-a-Verifier](https://arxiv.org/abs/2607.05391) | 连续评分与重复评估；量化复评的递减收益 | 不把复评预算接入在线 advantage estimator |
| [LLMs as a Jury](https://arxiv.org/abs/2607.10139) | 跨模型误差去相关及 shared-error floor | 对象是推理时选择，不是训练时 policy gradient |
| [Citation Verifier Benchmark](https://arxiv.org/abs/2607.08700) | 同 F1 下方向性偏差差异显著 | 尚未把方向性误差变成在线计算分配目标 |

保守表述应是：**截至 2026 年 8 月 21 日核验的一手来源，没有发现把三类动作联合成 GRPO 组内在线 acquisition problem 的论文。** 不能声称绝对无人做过；投稿前仍应再做一次最新 collision search。

## 预注册式证伪线

拒绝主假设，如果：

- 在数学与代码两个任务、早期与中期两个 checkpoint、至少 3 个种子上，联合分配器相对最强静态基线的 advantage 符号错误率平均下降不足 1 个百分点；并且
- held-out oracle score 或学习曲线面积提升不足 0.5 个百分点，95% 置信区间覆盖零；或者
- 学到的动作比例不随组内不确定性改变，始终退化为相同静态比例。

这条线足够严格：算法不能只在某个代理分数上变好，也不能用更多实际 FLOP 偷预算。

## 两个备选问题

### 备选一：低预算预测未来可训练性

> **能否用低预算的跨任务反事实 support probe，在正式第二阶段训练之前预测某个 RLVR checkpoint 的未来可训练性，并使 checkpoint 选择优于 pass@1、best@k、token entropy 与 KL divergence？**

价值：多阶段后训练越来越像 continual learning，但当前 checkpoint 通常按已学任务分数选择。[Verifier-Induced Support Reshaping](https://arxiv.org/abs/2608.00220) 已证明当前分数改善不保证后续目标仍可学；新意应放在低预算预测和 checkpoint-selection regret，而不是再次描述 support reshaping。

主要风险：这很可能被审稿人视为上述工作的自然延伸，所以需要跨模型、跨训练顺序、跨 BBG/ReCo/PAEC 干预证明 probe 不是特定设置指标。

### 备选二：自验证代理任务的有效性交换测试

> **对于把开放任务变成自验证代理游戏的 RLSVR，跨代理环境、检测器与外部评审器的交换测试，能否可靠区分真实任务质量提升与只对单一代理游戏规则的适配或投机？**

价值：[RLSVR/SpyRL](https://arxiv.org/abs/2607.23802) 已展示开放任务上的明显增益，但代理游戏是否学到了真实任务质量，不能只靠同一个代理或一个外部 judge 判断。用两个代理环境、检测器交换、异质 judge 与已知捷径阳性对照，可以把“代理有效性”变成可证伪的识别问题。

主要风险：第二个代理若调用不同能力，交换失败会混入 task mismatch；实验设计质量比模型规模更重要。

## 被淘汰的四个方向

| 候选 | 淘汰原因 |
| --- | --- |
| group-centering 下的相关 verifier error | 是主问题的关键机制消融，单独立项贡献面过窄 |
| generator/solver/verifier 同家族的循环确认 | 与工作簿 G23、G28、G40 直接重叠 |
| 方差轨迹预测 reward shortcut 的干预窗口 | [Dark Room](https://arxiv.org/abs/2607.21273) 已覆盖核心预测量和机制，跨任务复制不够新 |
| 同 F1 下方向性 bias 的在线后果 | 与工作簿 G13/G14 及 2026 citation-verifier 论文重叠，适合作为主问题消融 |

## 研究边界与诚实声明

- 本轮所有 2026 arXiv 论文均按预印本证据处理，结论仍可能在正式发表前变化。
- 工作簿是高召回池，不保证零遗漏；本轮在线检索补充了工作簿截止后和未充分展开的近邻工作。
- 主问题首先对 noisy/learned verifier 下的 group-relative 在线优化成立；不能自动外推到纯确定性 rule-based RLVR。
- 数学/代码的隐藏 oracle 只是最小因果测试。若要声称对开放任务有效，必须追加盲人评或预先冻结的异质高置信 jury。
- 结构化会话、20 条来源、8 个候选的完整评分与研究问题契约见同名 [JSON](2026-08-21-online-feedback-budget-allocation.json)。

## 关键一手来源

1. [Verifier-Induced Support Reshaping in On-Policy Optimization](https://arxiv.org/abs/2608.00220)
2. [Understanding Diversity Collapse in RLVR via the Lens of Overtraining](https://arxiv.org/abs/2606.15455)
3. [ReCo: Reweighting GRPO Against Distributional Concentration](https://arxiv.org/abs/2607.26862)
4. [PAEC: Position-Aware Entropy Calibration for LLM Reasoning in RLVR](https://arxiv.org/abs/2606.08543)
5. [BODHI: Do LLMs Branch Out and Discover Heterogeneous Inferences?](https://arxiv.org/abs/2608.02867)
6. [Where Should RL Post-Training Compute Go?](https://arxiv.org/abs/2607.13389)
7. [Early Verdicts, Better Budgets: SARA](https://arxiv.org/abs/2607.26253)
8. [Ask a Strong LLM Judge when Your Reward Model is Uncertain](https://arxiv.org/abs/2510.20369)
9. [LLM-as-a-Verifier](https://arxiv.org/abs/2607.05391)
10. [LLMs as a Jury](https://arxiv.org/abs/2607.10139)
11. [Do You Need a Frontier Model as a Citation Verifier?](https://arxiv.org/abs/2607.08700)
12. [RewardUQ](https://arxiv.org/abs/2602.24040)
13. [The Dark Room in the Reward Channel](https://arxiv.org/abs/2607.21273)
14. [From RLVR to RLSVR](https://arxiv.org/abs/2607.23802)
15. [LLM-as-a-Coach](https://arxiv.org/abs/2607.18110)
16. [Self-Verified Distillation](https://arxiv.org/abs/2605.26132)
17. [Don't Peek at the Answer: OM-GRPO](https://arxiv.org/abs/2608.03119)
18. [Improving Generalization Robustness of Multimodal RLVR](https://arxiv.org/abs/2608.08802)
19. [An Empirical Study of Reward Specification and Benchmark Reliability in GRPO-based LLM Unlearning](https://arxiv.org/abs/2608.17804)
