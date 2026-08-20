# Blind Mapping Review v0.4

本目录是 Candidate Generation v0.4 的条件盲人工映射复核材料，不调用模型 API。

## 给评审者

每位评审者只接收：

1. 最安全的分发方式是对应的 `reviewer-bundles/reviewer-*.zip`；
2. 解压后直接打开 `review-form.html`，逐组填写并导出 CSV；
3. 或使用 Markdown 评审包与 `reviewer-template.csv`。

不要打开 `coordinator/unblinding-key.json`，也不要查看原实验候选工件、自动映射、生成条件、模型种子、隐藏事实或下游结果。两位评审者独立完成全部 384 行，在提交前不讨论答案。

## 给协调者

```powershell
python blind_mapping_review.py validate-review blind-review-v0.4/forms/reviewer-1.csv
python blind_mapping_review.py validate-review blind-review-v0.4/forms/reviewer-2.csv
python blind_mapping_review.py prepare-adjudication `
  --reviewer-one blind-review-v0.4/forms/reviewer-1.csv `
  --reviewer-two blind-review-v0.4/forms/reviewer-2.csv `
  --output blind-review-v0.4/coordinator/adjudication.csv
```

仲裁者只填写 `adjudication.csv` 中的 `final_value`、`adjudicator_id` 和可选说明。之后运行：

```powershell
python blind_mapping_review.py analyze `
  --reviewer-one blind-review-v0.4/forms/reviewer-1.csv `
  --reviewer-two blind-review-v0.4/forms/reviewer-2.csv `
  --adjudication blind-review-v0.4/coordinator/adjudication.csv `
  --output blind-review-v0.4/analysis
```

正式阈值与评分口径见 `../BLIND-MAPPING-REVIEW-PROTOCOL-v0.4.md`。

Packet hash: `4e0cc330097015365c83c168a0cd8f763ca01b7f36af1254495e54c29eed84b7`
