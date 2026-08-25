# 模式 2 研究问题发现报告：Agentic GRPO 的结果依赖删失

- 日期：2026-08-25
- 引擎：模式 2（当前 Codex）
- 状态：完成
- 外部模型 API：未调用
- 原始工作簿：`project/GRPO-X1/LLM_posttraining_research_questions_2025_2026.xlsx`（只读）

## 先给结论

本轮最值得启动的研究问题是：

> 在长轨迹 agentic GRPO 中，当 rollout 因超时、工具错误或调度器提前终止而不能完整返回，且这种“能否返回”依赖轨迹长度、行为和潜在成败时，只在幸存轨迹上计算组内基线与标准化会产生多大的结果依赖删失偏差？在固定生成预算下，显式记录 rollout 状态并采用续跑/回收或删失感知估计，能否恢复完整 rollout oracle 的更新方向并改善分布外成功率？

它比“超时样本记 0 分还是丢弃”更有研究价值，因为核心对象不是一个经验规则，而是一个可推导、可测量、可被证伪的统计机制：**完成概率是否与模型行为和潜在奖励相关，以及这种选择过程是否改变 GRPO 的相对优势和参数更新方向。**

最近工作已分别处理长尾吞吐、partial group 的软件错分、提前终止、group-size/归一化方差，但本轮检索未发现一项工作同时把“非随机未完成 → 幸存者组内归一化 → 更新偏差 → 删失感知修正”作为主问题并与完整 rollout oracle 对照。因此它目前是“值得做最小实验”，不是“已经证明新颖”。

## Stage 0：冻结决策目标

- 使用者：大模型文本后训练、数据合成、GRPO/RLVR 与 agentic RL 研究者。
- 决策：训练系统遇到 timeout/error/early-stop 时，应继续简单丢弃或记失败，还是把完成状态作为训练数据的一部分并使用删失感知更新。
- 时间：一周内完成无训练或单步更新的 cheap probe；只有机制信号稳定才启动短程训练。
- 资源边界：首轮使用一个 1B–3B 模型、可控的数学/代码或轻量 agent 环境、已有完整 rollouts；不先做大规模训练。
- 停止条件：如果在现实删失率下，幸存者更新与完整 oracle 的梯度方向、prompt 排序和短程学习结果没有稳定差异，则停止扩大。

## Stage 1：现实信号

### 本地版图

工作簿含 5,608 条完整记录、940 条核心问题、240 条 2026 arXiv 和 42 条综合研究空白。它适合做高召回趋势图和重复约束，不是系统综述：论文问题字段中有模板化表述，42 个空白的证据论文是关键词自动关联，不能被当作论文作者直接提出了同一问题。

最相关的既有空白是 G35（GRPO 有效全局 batch 的分解）、G03（batch/group 构成与梯度冲突）、G34（随机种子与 rollout 顺序复现）、G06（长轨迹信用分配）和 G38（agentic RL 评估）。工作簿没有把“未完成轨迹是结果依赖删失”单独建模。

### 一手信号的观察—解释—未知

1. [AReaL partial-group issue](https://github.com/areal-project/AReaL/issues/1419) 证明现实框架会保留不完整组；固定步长重建会跨 prompt 混组或产生极端 advantage。解释：未完成不是假想边缘情况。未知：修复分组身份以后，保留幸存轨迹本身是否仍有统计偏差。
2. [TRL rollout-source RFC](https://github.com/huggingface/trl/issues/5974) 已把 `ok/timeout/context_overflow/error` 设计成显式状态。解释：系统层已经需要区分失败类型。未知：这些状态进入 GRPO 目标时应如何估计。
3. [APRIL](https://arxiv.org/abs/2509.18521) 通过过量发起、达到目标数量即停止并回收未完成 rollout，强调不丢轨迹。解释：长尾调度会改变哪些轨迹在本轮“幸存”。未知：回收相对简单丢弃是否主要改善吞吐，还是也避免了学习偏差。
4. [Selective Rollout](https://arxiv.org/abs/2605.05802) 用中途轨迹相似度提前终止预计零方差的组。解释：终止决策正在依赖轨迹内容。未知：当预测器误差与终局奖励相关时，对目标的偏差是多少。
5. [Rollout Pass-Rate Control](https://arxiv.org/abs/2605.05112) 表明 group survival、advantage energy 与 pass rate 强相关。解释：哪些组留下学习信号会改变训练信息分布。未知：系统失败导致的 survival 是否与这种算法过滤叠加。
6. [TRL GRPO Trainer](https://huggingface.co/docs/trl/main/en/grpo_trainer) 暴露多种 loss reduction 语义。解释：token、sequence、batch、group 的归约单位会改变更新。未知：这些实现是否都对 rollout partition 与缺失状态保持一致。
7. [SFT-then-RL Outperforms Mixed-Policy Methods](https://arxiv.org/abs/2604.23747) 报告微批丢失与 loss aggregation bug 能反转公开基线结论。解释：训练实现的静默语义差异足以改变科研结论。未知：GRPO 特有的组统计和非随机缺失是否形成另一类系统性偏差。
8. [Infinite Sampling](https://arxiv.org/abs/2506.22950) 把大组拆成微采样组以降低显存，同时保留完整生成。解释：完整组统计与执行分批可以解耦。未知：跨训练框架、不同更新分区能否严格保持同一更新。
9. [ΔL Normalization](https://arxiv.org/abs/2509.07558) 将变长输出下的 loss 聚合写成最小方差无偏估计。解释：归约单位不是实现细节。未知：论文不处理未完成轨迹的选择机制。
10. [Single-stream Policy Optimization](https://openreview.net/forum?id=DxSn373nRU) 将 GRPO 的小组 baseline/std 识别为高方差来源并使用历史 baseline 与全局标准化。解释：小组统计对样本集合高度敏感。未知：全局标准化是否能修复非随机删失，还是只降低方差。
11. [Hierarchy-of-Groups Policy Optimization](https://openreview.net/forum?id=T8Dev99qnz) 指出长程 agent 步级样本的历史上下文不一致会使 advantage 有偏。解释：agentic 轨迹的“什么才是可比组”本身需要建模。未知：没有处理 timeout/error 造成的 outcome-dependent survival。
12. [RL with Verifiable yet Noisy Rewards under Imperfect Verifiers](https://arxiv.org/abs/2510.00915) 研究不完美验证器下的非对称 reward noise。解释：观察到的 0/1 与潜在正确性可分离。未知：reward noise 与 completion censoring 同时出现时的可识别性尚不清楚。

## Stage 2：5W1H 与苏格拉底重构

### 5W1H

- Who：训练框架维护者与运行长轨迹 agentic GRPO 的研究者。
- What：每个 prompt 原计划的 G 条 rollout 中，哪些完整返回、哪些 timeout/error/early-stop，以及幸存集合如何进入 baseline、std 和 loss reduction。
- When：rollout 生成、异步收集、组重建、reward 计算和 policy update 之间。
- Where：工具使用、代码执行、网页交互等长尾且状态化的环境，短答案 RLVR 作为负对照。
- Why：若完成概率依赖长度、行为或潜在结果，则仅训练幸存者相当于非随机选择，可能系统性强化“更快返回”而非“更会完成任务”。
- How：保存原始 G 个 attempt 的身份和状态，以完整运行得到的终局结果作为 oracle，再人工重放多种删失机制，比较更新方向与短程训练。

### 六个改变结论的问题

1. “未完成”是独立基础设施故障，还是由策略行为、轨迹长度和任务难度共同决定？
2. 研究目标是完成时限内的在线效用，还是无限等待下的任务正确率？前者可能使 timeout=失败成为正确目标，后者则是删失。
3. 训练系统当前是丢单条轨迹、丢整个组、记 0 分，还是跨步回收？
4. 在已完整生成的数据上模拟同样的删失，幸存者更新与 full oracle 的梯度余弦和符号翻转率有多大差异？
5. 差异来自错误组重建、group std 波动、长度加权，还是完成机制本身？
6. 哪个反转证据会停止项目？答案是：身份正确、归约一致后，在现实删失率和三类机制下仍无稳定偏差。

重构后的真问题不是“系统如何更快”，而是：**agentic RL 的执行状态是否隐式改变了被优化的统计总体；如果改变，应该优化时限效用还是校正回完整任务效用。**

## Stage 3：QFT 扩展与 STORM

QFT 先不评价地生成 12 个问法：

1. 幸存者组内标准化是否偏离完整 rollout 目标？
2. timeout=0、drop-one、drop-group、resume 四种语义学习到什么不同策略？
3. 完成概率能否作为 propensity 估计并做 inverse-probability correction？
4. doubly robust estimator 能否同时利用终局 reward 模型与完成模型？
5. partial-group 身份修复后还剩多少统计偏差？
6. 同一 rollout batch 改变 microbatch/accumulation partition 是否保持参数更新？
7. token/sequence/prompt/group reduction 哪一层破坏 partition invariance？
8. verifier 重复评分能否分离 reward noise 与 response signal variance？
9. 去除 std、global std、noise-debiased std 何时分别最优？
10. prompt batch 构成是否通过 group-normalized gradient conflict 改变学习？
11. 多轮 agent step 应按 root prompt、trajectory 还是历史 context 分组？
12. 提前终止策略是否在节省计算时改变了训练目标？

STORM 使用六个互补视角：

- 统计视角要求区分 missing completely at random、条件随机缺失和结果依赖删失。
- 优化视角要求把 bias、variance、gradient cosine 与长期回报分开。
- 系统视角要求记录 straggler、timeout、context overflow、tool error 和主动 early-stop。
- agent 视角指出“慢”可能是探索、循环、工具等待或复杂正确路径，不能一律等同失败。
- 评测视角要求先写清业务效用：deadline-constrained success 与 eventual success 是不同 estimand。
- 反方审稿视角质疑这只是一个框架 bug；因此必须给出形式化目标、跨实现复现和能改变训练策略的修正。

## Stage 4：机制与证据聚类

12 个问法合并为 10 个正式候选：

- A 删失机制：C01 结果依赖删失；C04 失败状态的奖励语义。
- B 更新一致性：C02 partition invariance；C10 跨框架科研结论复现。
- C reward/advantage 估计：C03 噪声分解归一化；C06 有效 group size。
- D 数据构成：C05 prompt-batch 梯度冲突。
- E 长程分组：C07 历史上下文一致性。
- F 已被近期工作直接覆盖：C08 零方差组提前终止；C09 变长 loss 聚合。

C04 被保留为 C01 的关键边界而非独立主问题；C10 是 C02 的应用层；C08、C09 因直接碰撞降级。

## Stage 5：双向钢人与竞争假设

### 支持主问题的最强论证

长轨迹 agent 的完成时间、工具错误和 context overflow 明显受策略行为影响。若慢而复杂的正确轨迹更容易 timeout，或循环失败轨迹更容易被调度器抢先终止，幸存集合就不是原策略的随机样本。GRPO 又用同组幸存者计算均值和标准差，因此一条轨迹消失不仅删除自身梯度，还改变同组所有轨迹的 advantage。AReaL 的 partial-group 事件、APRIL 的回收设计和内容依赖提前终止共同说明该条件现实存在。只要更新方向可被稳定改变，系统调度就已成为未声明的算法超参数。

### 反对主问题的最强论证

在有明确服务时限的 agent 产品中，timeout 本来就是失败，记 0 分并非删失；丢弃通常只是坏实现，可通过保留组身份、丢整个不完整组或续跑解决。即使单步梯度变化，policy-gradient 本身方差很高，长期训练可能平均掉。近期工作已处理 partial group、长尾回收、提前终止和归一化，新的工作容易被审稿人视为把成熟的 survival-analysis 术语套在系统问题上。

### 真正分歧与关键变量

真正分歧不是“要不要保留 timeout”，而是目标 estimand：优化 deadline 内成功，还是完整任务成功。最可能改变结论的变量是 completion probability 与潜在 reward/轨迹特征的条件相关性；其次是删失率、group size、删失发生在组中哪个位置，以及框架使用的 reduction 语义。

### ACH 竞争假设

- H1 结果依赖删失：即使组身份和 loss reduction 都正确，survivor-only update 仍系统偏离 full oracle。
- H2 纯软件错分：偏差全部来自 partial group 的身份重建 bug；修复后消失。
- H3 目标定义差异：timeout=0 正确优化了 deadline utility，与 eventual-success oracle 不同但不是估计错误。
- H4 归约/长度偏差：差异由 token/sequence weighting 或 std 噪声解释，而非完成选择。
- H5 有限样本波动：单步差异不稳定，跨种子与短程训练不复现。

判别顺序：先固定组身份与同一 loss reducer，再比较 MCAR、长度依赖、reward 依赖和 tool-error 依赖删失；随后同时报告 deadline utility 与 eventual success，避免把目标变化误叫估计偏差。

## Stage 6：硬门槛与排序

所有入选前三名均通过来源支撑、答案改变行动、中立、可回答和伦理五项硬门槛。六维总分（每项 0–2）如下：

| 候选 | 决策杠杆 | 判别力 | 现实支撑 | 可回答 | 新颖/不重复 | 成本收益 | 总分 | 处理 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| C01 结果依赖删失 | 2 | 2 | 2 | 2 | 2 | 2 | 12 | 主问题 |
| C02 更新分区不变性 | 2 | 2 | 2 | 2 | 1 | 2 | 11 | 备选 |
| C03 噪声分解归一化 | 2 | 2 | 2 | 2 | 1 | 2 | 11 | 备选 |
| C04 失败状态语义 | 2 | 2 | 2 | 2 | 1 | 1 | 10 | 并入 C01 |
| C05 prompt-batch 梯度冲突 | 2 | 1 | 2 | 2 | 1 | 1 | 9 | 保留 |
| C06 有效 group size | 1 | 2 | 2 | 2 | 0 | 1 | 8 | 缩小 |
| C07 多轮历史分组 | 2 | 1 | 2 | 1 | 0 | 1 | 7 | 缩小 |
| C08 零方差提前终止 | 1 | 1 | 2 | 2 | 0 | 1 | 7 | 淘汰 |
| C09 变长 loss 聚合 | 2 | 2 | 2 | 2 | 0 | 1 | 9 | 淘汰 |
| C10 跨框架复现 | 2 | 2 | 2 | 1 | 0 | 2 | 9 | 改写进 C02 |

## Stage 7：前三名的廉价现实试探

### C01：结果依赖删失

拿一批已经完整跑完、带每步时间和终局 reward 的 G-way rollouts 作为 oracle。离线施加四种删失：随机、长度依赖、reward 依赖、tool-state 依赖；比较 drop-one、drop-group、timeout=0、resume/recycle、IPCW/AIPW 的梯度余弦、advantage 符号翻转、prompt 排序和估计误差。若只有错误组身份导致差异，改写为工程测试；若正确身份下 outcome-dependent 机制仍稳定偏离，则保留。

### C02：更新分区不变性

冻结同一 rollout、old log-prob、reward、optimizer state，只改变 microbatch、gradient accumulation 和设备分区；在 TRL/verl/OpenRLHF 或最小参考实现中比较 loss、gradient 与 parameter delta。若同一明示 objective 在所有分区下数值误差内一致，淘汰；若差异只来自已知 bug，缩小为 conformance suite；若多个框架存在未声明的目标变化，保留。

### C03：噪声分解归一化

对同一 response 重复评分估计 verifier noise，分解 group reward variance 为 response signal 与测量 noise；比较标准 GRPO std、仅中心化、global std 和 noise-debiased std 对 oracle-gradient 的误差。若改进被现有 no-std/global normalization 完全覆盖，淘汰；若在相同观测 reward 质量下可预测选择规则，保留。

反向检索结果：C02 与微批/聚合 bug、Infinite Sampling、ΔL 高度相邻，因此新颖性仅给 1；C03 与 SPO、去 std 和 noisy verifier 工作相邻，也只给 1；C01 尚未找到直接同构工作，因此优先。

## Stage 8：研究问题契约

### 最终问题

在长轨迹 agentic GRPO 中，当 rollout 因超时、工具错误或调度器提前终止而不能完整返回，且完成概率依赖轨迹长度、行为和潜在成败时，只在幸存轨迹上计算组内基线与标准化会产生多大的结果依赖删失偏差？在固定生成预算下，显式记录 rollout 状态并采用续跑/回收或删失感知估计，能否恢复完整 rollout oracle 的更新方向并改善分布外成功率？

### 概念边界

- “删失”仅指本来存在潜在终局结果，但当前执行预算内未观察到；若业务目标就是 deadline 内成功，timeout=0 是另一个合法 estimand。
- “幸存者更新”要求组身份已正确，不把 AReaL 式跨组错分算作统计机制。
- “完整 oracle”来自可控环境中让所有 attempt 跑完，或从完整数据离线模拟删失；不是用另一个 LLM judge 猜终局。
- 首轮固定 rollout、old policy、reward 和 optimizer，先测 estimator；再做 50–100 step 短程训练。
- 报告 eventual success、deadline success、平均 token/秒、失败类型和 OOD，不只报告 reward。

### A/B/未知与行动

- A：正确分组后，非随机删失仍稳定改变 advantage 符号和更新方向，删失感知方案更接近 full oracle。行动：框架必须保留 attempt 状态与原始 group membership；优先 resume/recycle，在可识别条件下评估 IPCW/AIPW，并将调度策略写入算法配置。
- B：差异只来自软件错分或有限样本，timeout=0/drop-group 与 oracle 在目标一致时无稳定差别。行动：做严格 conformance check 后使用简单语义，不引入高方差修正。
- 未知：只在高删失率、特定工具或长正确轨迹上出现。行动：缩小到这些边界，并将 completion-rate/特征相关性设为启用阈值。

### 最小实验

1. 从一个可控的代码或工具环境生成约 500 prompts × 8 完整 rollouts，保存 step latency、tool status、长度、reward 和 root prompt id。
2. 预注册四种删失机制和 5%、15%、30% 三档删失率；真实 timeout 分布作为第五种。
3. 固定 policy/optimizer，测各种处理方案相对 full oracle 的 gradient cosine、相对 L2 error、advantage-sign flip、prompt influence 与 deadline/eventual 两种目标误差。
4. 只有 C01 在至少两类非随机删失、三个种子上稳定成立，才跑 50–100 step 训练；比较 full、drop-one、drop-group、zero、resume 和最优 censor-aware estimator。
5. 若修正提升 eventual success 却损害 deadline success，应把结果表述为 estimand trade-off，而不是算法普遍优越。

### 反转与停止条件

- 组身份与 loss reduction 修复后，所有现实删失机制下 gradient cosine 均接近 1、符号翻转无系统性，停止。
- 效应完全由长度加权或 std 估计解释，主问题改写为 C02/C03。
- completion propensity 无法从删失前信息估计，IPCW/AIPW 不作主干预，改用 resume/recycle 或界限分析。
- 单步差异存在但 50–100 step 的方向跨种子不一致，不扩大模型规模。
- 已出现直接同构论文且覆盖机制、oracle 和干预，则停止新颖性主张。

## 两个备选问题

1. **C02 更新分区不变性**：在固定 rollout、旧策略、optimizer state 与总 token 时，仅改变同一次 GRPO update 的 microbatch/gradient-accumulation partition，主流实现是否产生不同参数更新；能否用明确的 reduction-unit 语义和 conformance test 恢复分区不变性？
2. **C03 噪声分解归一化**：当 verifier noise 占据 group reward variance 的主要部分时，利用重复评分估计并扣除测量噪声的 advantage scaler，能否比标准 std、仅中心化和全局标准化更接近潜在真实 reward 的 policy gradient？

## 明天即可开始的动作

不要先训练模型。先从现有 agent rollout 日志中抽一批“最终都完成”的 G-way 轨迹，保留原始完成时间；用 30 分钟、60 分钟等虚拟 deadline 重新标出谁会被删失，然后比较 full-group 与 survivor-only 的 advantage 符号翻转率和梯度余弦。如果连这一层都没有稳定差异，项目可以低成本结束；如果差异明显，再实现修正。
