# 历史候选报告（非正式模式 2）：严格验证会不会筛掉正确的少数解法

> 身份更正：本报告使用的是已撤销的 Codex 自主六阶段流程，没有执行与模式 1 相同的共享问题发现漏斗。内容可作为候选材料参考，但不能声称已经过正式模式 2。

日期：2026 年 8 月 21 日

范围：LLM 文本后训练、合成推理数据、自验证过滤、SFT/RFT、验证器与 OOD 泛化

本地输入：[LLM_posttraining_research_questions_2025_2026.xlsx](../../../project/GRPO-X1/LLM_posttraining_research_questions_2025_2026.xlsx)

## 一句话结论

本轮最值得研究的问题，用最简单的话说是：

> **合成推理数据审得越严，真的越好吗？严格验证会不会把“少见但正确”的解法也筛掉，最后让训练数据越来越单一、模型的分布外泛化反而变差？**

正式研究问题是：

> **在合成推理数据管线中，增加重复验证次数并要求更高的一致通过率，是否会在提升标签精度的同时，系统性删除验证成本高但实际正确的少数推理模式，压低梯度空间多样性和 OOD 泛化；在相同接受样本数与总算力下，“正确性下限 + 梯度多样性约束”的筛选是否优于全票通过？**

这里的核心不是“多留一些不同措辞”，而是保留**真正不同的解题路线**。例如一道数学题有代数法、几何法和构造法；如果验证器更熟悉代数模板，全票通过可能得到很干净的数据，却把后两种正确路线不断误杀。

## 为什么现在值得做

这个问题来自两条最新工作之间一个很具体的缺口：

- [Self-Verified Distillation](https://arxiv.org/abs/2605.26132) 证明，增加候选生成和重复验证通常能得到更好的自训练数据；但论文也发现，验证太严而候选太少时会留下过少数据，并明确承认保守验证可能拒绝有用答案、对不同领域不均匀。
- [Prismatic Synthesis](https://arxiv.org/abs/2505.20161) 证明，基于样本梯度方向的多样性指标 G-Vendi 在控制数据质量和规模后，与推理任务 OOD 泛化高度相关；论文还明确把“验证协议、质量过滤怎样改变数据多样性和模型表现”列为未解方向。

因此，已知的是：

1. 严格验证能提高数据正确率；
2. 有用的推理多样性会影响 OOD 泛化；
3. 严格验证会减少覆盖。

**还没有被干净回答的是**：减少的覆盖只是“数据变少了”，还是验证器在系统性误杀某些正确推理模式？如果是后者，能否在不降低正确率的情况下修复？

## 它和工作簿已有问题有什么不同

工作簿包含 5,608 条高召回文献、940 条核心论文、240 条 2026 arXiv 和 42 个已整理研究空白。最接近的已有方向是：

- G24：正确性、多样性、难度和新颖性的 Pareto 前沿；
- G25：多轮自合成与自训练的数据分布坍缩；
- G26：语义去重应去掉多少相似轨迹；
- G29：用验证器不确定性和学习进度决定下一批合成什么；
- G18：多个验证器冲突时怎样区分多解、盲区和投机。

这些方向都重要，但本轮不再问宽泛的“质量与多样性怎么平衡”。它研究一个更窄、能做因果对照的机制：

> **固定同一批候选，把不同验证严格度调到相同接受数据量和相同真实正确率，再看它们是否保留了不同数量的正确推理模式。**

只要做到这三个匹配——同一候选池、同 precision、同 token/FLOP——就能把“严格验证的选择压力”从普通的数据量和噪声效应中分离出来。

## 主假设与最强反解释

### 主假设

验证器不是中性的漏勺。它更容易一致接受熟悉、常见、模板化的推理；对罕见但正确的路线更容易产生分歧。因此，要求多次判断或多模型全票通过时：

- 接受集正确率会上升；
- 但 oracle-correct 轨迹的误拒绝会集中在梯度空间稀疏簇和少见语义分支；
- 即使接受数据量和标签精度相同，严格共识集的 G-Vendi 和 OOD 收益仍会更低；
- “先满足正确性下限，再最大化梯度多样性”的筛选会改善 OOD，同时保持 ID 和标签精度。

### 反假设

严格验证主要只是正确去噪。少见正确路线并没有更高误拒绝率；观察到的多样性下降完全来自数据变少，或来自宽松筛选混入更多错误。只要匹配数据量和真实正确率，全票通过与多样性约束就没有稳定差异。

这条反假设很强，也容易让实验失败，所以问题是可证伪的。

## 最小实验怎么做

### 阶段 A：先证明有没有“误杀正确模式”

数据和模型：

- 约 4,000 个训练 prompt、1,000 个冻结测试 prompt；
- 数学精确答案和代码隐藏单元测试两类任务；
- 两个不同模型家族的 7B–14B 生成器；
- 每题每个生成器采样 8 条轨迹，共约 16 条候选；
- 三个不同模型家族的 verifier，每条轨迹重复判断 5 次。

数学答案和单元测试只作为隐藏 oracle，用来研究哪些样本真的正确；筛选算法本身不能看到 oracle。

在同一个冻结候选池上构造：

1. 不筛选；
2. 单 verifier 单次判断；
3. 同 verifier 重复 5 次；
4. 三个 verifier 中 1/3、2/3、3/3 通过；
5. [Weaver](https://arxiv.org/abs/2506.18203) 式加权 panel；
6. oracle filter，只作为上限；
7. 正确性下限 + G-Vendi；
8. 正确性下限 + 语义分支多样性。

先不训练大模型，只测：

- accepted precision 和 recall；
- 实际正确轨迹的 false-rejection rate；
- 误拒绝是否集中在稀疏梯度簇、少见语义分支、高验证成本轨迹；
- G-Vendi、语义分支熵、梯度簇覆盖；
- 每种筛选的生成/验证 FLOP、接受 token 和 wall-clock。

### 阶段 B：再验证是否真的影响训练

从同一候选池构造约 20k–40k 条训练集。所有方法必须匹配：

- 接受 token 数；
- 隐藏 oracle 下的 accepted precision；
- 总生成 + 验证 FLOP；
- student 的训练 token、优化器、学习率和 checkpoint 规则。

用同一个 1.5B–3B student、至少三个随机种子做 SFT。主指标是跨题型、跨模板、跨生成器家族的 OOD；ID pass@1 是 guardrail，避免用牺牲常规能力换取一个漂亮的多样性分数。

预计算力：4 张 A100 80GB 或等效算力，约 4–8 GPU-days。候选和判断可以离线批量生成，因此第一阶段失败时，不需要浪费完整训练成本。

## 严格的证伪线

拒绝主假设，如果同时出现：

- 在数学和代码、两个生成器家族、至少三个种子上，提高共识严格度后，oracle-correct 轨迹的 G-Vendi 或语义分支覆盖相对等规模宽松筛选平均下降不足 5%；
- 梯度稀疏簇的误拒绝率不高于常见簇；
- 正确性下限 + G-Vendi 相对最强的严格/加权 panel 基线，OOD 平均提升不足 1 个百分点，或 95% 置信区间覆盖零。

另外，如果多样性方法只是通过降低标签正确率、使用更多训练 token 或偷用更多验证 FLOP 获益，也算机制失败。

## 预期贡献

如果成立，论文的贡献可以收敛为三个部分：

1. **机制**：提出 `verification selection pressure`——验证器不仅去噪，也会改变正确训练数据的支持分布。
2. **测量**：给出正确样本误拒绝、verification cost、precision-matched G-Vendi 和语义分支覆盖的诊断协议。
3. **方法**：提出“正确性下限 + 梯度多样性约束”的筛选器，在相同数据量、正确率和算力下替代全票通过。

如果不成立，结论也有价值：它会说明，在严格匹配质量与规模后，全票验证没有隐藏的推理多样性代价，合成管线可以放心把预算用于更强验证。

## 最近工作碰撞

| 最近工作 | 已经解决 | 本问题保留的非重复增量 |
| --- | --- | --- |
| [Self-Verified Distillation](https://arxiv.org/abs/2605.26132) | 生成数量 × 重复验证强度；全票多阶段过滤；下游 pass@1 | 不识别哪些 oracle-correct 模式被误拒；不做 precision-matched 梯度多样性筛选 |
| [Prismatic Synthesis](https://arxiv.org/abs/2505.20161) | G-Vendi 预测 OOD；在梯度空间合成稀缺样本 | 没有研究验证协议如何改变正确样本的梯度支持；论文明确将其列为未来方向 |
| [SPARQ](https://arxiv.org/abs/2506.06499) | 合成问题的 quality-diversity 算法；质量偏 ID、多样性偏 OOD | 不研究 verifier consensus 的选择偏差 |
| [AdaSTaR](https://arxiv.org/abs/2505.16322) | 平衡题目采样并匹配模型能力；发现困难题会增加错误 CoT | 多样性是 observation 级，不是 oracle-correct 轨迹内部的验证误拒绝 |
| [LLMs as a Jury](https://arxiv.org/abs/2607.10139) | 测试时跨模型错误去相关和 shared-error floor | 不测训练数据的正确模式覆盖和 OOD 蒸馏收益 |
| [BODHI](https://arxiv.org/abs/2608.02867) | 用语义树测真实推理分支；发现 RLVR 后分支收缩 | 不研究离线验证过滤 |
| [Verifier-Induced Support Reshaping](https://arxiv.org/abs/2608.00220) | 在线 RLVR 会重塑未来可奖励支持 | 对象是在线策略优化，不是离线合成数据筛选和等 precision 识别 |
| [TrajFusion](https://arxiv.org/abs/2602.04391) | 把错误轨迹、反思和正确轨迹融合训练 | 研究被拒绝错误的利用；本问题研究实际正确轨迹被误拒 |

保守新颖性表述应为：**截至 2026 年 8 月 21 日核验的一手来源，没有发现对合成推理数据做“同候选、同 accepted precision、同 token/FLOP”的 verifier-stringency 选择偏差实验，也没有发现以质量下限 + G-Vendi 修正该偏差的完整研究。** 投稿前仍需再次做最新 collision search。

## 两个备选问题

### 备选一：难验证的正确样本是否更有训练价值

> **在已由隐藏 oracle 确认正确的合成推理轨迹中，“被验证器可靠通过所需的模型家族数和重复判断数”这一验证成本，能否识别少见但有下游训练价值的推理模式，并比单次 verifier 分数、损失、难度和表面多样性更好地预测 OOD 边际收益？**

直觉是：很容易通过的正确样本可能只是常见模板；适度难通过的正确样本可能包含验证器不熟悉但对泛化有价值的路线；极难通过的样本又可能含糊或过程有缺陷。因此预期可能是倒 U 型关系。

最大的优点是：只在 oracle-correct 样本内部做分析，可以排除“难验证只是因为更错”这个替代解释。最大风险是 verification cost 只是长度和文风指标，换 verifier 后不稳定。

### 备选二：该买更多重复判断，还是更多模型家族

> **在接受集标签精度相同的条件下，跨模型家族 jury 是否比同一模型的重复自验证更少误拒绝稀有但正确的推理模式，并因此产生更高梯度多样性和更强 OOD 蒸馏收益？**

[LLMs as a Jury](https://arxiv.org/abs/2607.10139) 已证明跨家族错误去相关有利于测试时选择；新意必须放在训练数据的正确模式覆盖，并调阈值匹配 accepted precision。若只证明“jury 更准”，贡献不够。

## 被淘汰的四个方向

| 候选 | 淘汰原因 |
| --- | --- |
| 生成数量与验证强度怎样分预算 | Self-Verified Distillation 已直接做 n×v 消融并报告交互 |
| 正确性、多样性、难度的宽泛 Pareto | SPARQ、Prismatic Synthesis 与工作簿 G24 已覆盖 |
| 按题目覆盖/成功率/进步率做 STaR 课程 | AdaSTaR 及工作簿 G09/G29 已覆盖 |
| 被拒绝错误轨迹应丢弃、修复还是融合 | TrajFusion 与工作簿 G30 已覆盖核心问题 |

## 边界与诚实声明

- 本轮不是把上一轮备选换个说法，而是重新执行的一次独立模式 2；上一轮的反馈算力分配、未来可训练性 probe 和 RLSVR proxy swap 都被排除。
- 2026 arXiv 工作均按预印本证据处理，结论可能继续变化。
- 数学和代码的隐藏 oracle 用于因果诊断；开放问答没有完整 oracle，不能直接照搬结论。
- 如果验证器是覆盖所有合法解法的确定性符号检查器，结构性误拒绝可能接近零。
- G-Vendi 只能在数据质量可比时解释，因此等 accepted precision 是主实验不可删除的控制。
- 多样性不是越高越好，质量下限、ID guardrail 和训练稳定性必须同时满足。
- 完整的 15 条来源、8 个候选、硬门槛、评分和实验契约见同名 [JSON](2026-08-21-verification-stringency-gradient-diversity.json)。

## 关键一手来源

1. [Self-Verified Distillation](https://arxiv.org/abs/2605.26132)
2. [Prismatic Synthesis](https://arxiv.org/abs/2505.20161)
3. [SPARQ](https://arxiv.org/abs/2506.06499)
4. [AdaSTaR](https://arxiv.org/abs/2505.16322)
5. [Beyond Rejection Sampling: Trajectory Fusion](https://arxiv.org/abs/2602.04391)
6. [ReSyn](https://arxiv.org/abs/2602.20117)
7. [LLMs as a Jury](https://arxiv.org/abs/2607.10139)
8. [BODHI](https://arxiv.org/abs/2608.02867)
9. [Understanding Diversity Collapse in RLVR](https://arxiv.org/abs/2606.15455)
10. [Verifier-Induced Support Reshaping](https://arxiv.org/abs/2608.00220)
11. [Multi-Agent Verification](https://arxiv.org/abs/2502.20379)
12. [Shrinking the Generation-Verification Gap with Weak Verifiers](https://arxiv.org/abs/2506.18203)
13. [RewardUQ](https://arxiv.org/abs/2602.24040)
14. [Know When To Fold 'Em](https://arxiv.org/abs/2605.14062)
15. [AI Evaluation Should Measure Verification Cost, Not Correctness Alone](https://arxiv.org/abs/2608.08709)
