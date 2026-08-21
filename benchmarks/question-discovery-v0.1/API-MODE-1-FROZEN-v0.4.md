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

## 与模式 2 的隔离

模式 1：

- 不读取模式 2 的提示、进度或结果；
- 不调用或导入模式 2 的 Codex 直接任务协调器；
- 继续只使用 `audit_judge_a_*`、`audit_judge_b_*`、`audit_arbitrator_*` 槽位；
- 可由统一入口选择，但实际在独立 Python 进程中运行。

模式 2 是当前 Codex 直接处理，不是另一套 API。两种模式只有在各自完成后，才允许由只读比较器读取最终 CSV/JSON。比较结果不能反向修改任一模式的评分。
