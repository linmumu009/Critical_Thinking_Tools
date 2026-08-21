# API 模式 1：冻结说明

日期：2026-08-20

状态：语义冻结；已有正式结果

## 身份

API 模式 1 是 v0.24.0 已完成的“双条件盲评审＋字段级自动仲裁”流程：

- 入口：`automated_mapping_audit.py`；
- 协议：[AUTOMATED-MAPPING-AUDIT-PROTOCOL-v0.4.md](AUTOMATED-MAPPING-AUDIT-PROTOCOL-v0.4.md)；
- 固定输出：[automated-review-v0.4/](automated-review-v0.4/)；
- 固定调度种子：`20260824`；
- 固定角色种子段：Judge A `610000`、Judge B `620000`、Arbitrator `630000`；
- 正式结果提交：`dade33c87db690064c13e9288ae95c1ae102d29f`。

“冻结”表示模式 1 的输入可见性、评分字段、提示角色、仲裁规则、随机种子和结果目录不再为适配模式 2 而改变。必要的软件错误修复必须单独升级版本并在 README 说明，不能静默改写现有结果。

## 与业务模式 2 的隔离

模式 1：

- 不读取模式 2 的研究画像、候选问题或研究结论；
- 不调用或导入模式 2 的研究问题会话协调器；
- 继续只使用 `audit_judge_a_*`、`audit_judge_b_*`、`audit_arbitrator_*` 槽位；
- 可由统一入口选择，但实际在独立 Python 进程中运行。

模式 2 是当前 Codex 执行的研究问题发现业务，不是本候选映射审计的另一后端，两者不做逐项评分比较。模式 1 的正式结果与冻结语义不因模式 2 改变。
