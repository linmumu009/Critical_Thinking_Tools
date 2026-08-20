from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import audit_candidate_results
import candidate_benchmark


ROOT = Path(__file__).resolve().parent
DEFAULT_PROGRESS = [
    ROOT / "results" / f"candidate-v0.4-seed{seed}.json" for seed in (1, 2, 3)
]
DEFAULT_OUTPUT = ROOT / "blind-review-v0.4"
GENERATOR_CONDITIONS = tuple(candidate_benchmark.GENERATOR_FILES)
REVIEW_FIELDS = (
    "mapped_evidence_id",
    "atomic_single_observation",
    "fully_answerable_by_mapping",
    "distinct_from_other_candidates",
    "action_discriminating",
)
BINARY_FIELDS = REVIEW_FIELDS[1:]
REVIEW_COLUMNS = (
    "packet_hash",
    "reviewer_id",
    "review_id",
    "candidate_id",
    *REVIEW_FIELDS,
    "notes",
)
ADJUDICATION_COLUMNS = (
    "packet_hash",
    "review_id",
    "candidate_id",
    "field",
    "reviewer_one_id",
    "reviewer_one_value",
    "reviewer_two_id",
    "reviewer_two_value",
    "final_value",
    "adjudicator_id",
    "notes",
)
RANDOMIZATION_SEED = 20260823
MATERIAL_MAPPING_DISAGREEMENT_RATE = 0.10
MIN_REVIEWER_EXACT_MAPPING_AGREEMENT = 0.85
MIN_REVIEWER_MAPPING_KAPPA = 0.70


def load_json(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if '"api_key"' in text or "Bearer " in text:
        raise ValueError(f"credential-like field found in {path}")
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.parent.parent).as_posix()
    except ValueError:
        return path.name


def resolve_recorded_path(raw_path: str, fallback_dir: Path) -> Path:
    recorded = Path(raw_path)
    if recorded.exists():
        return recorded.resolve()
    fallback = fallback_dir / recorded.name
    if fallback.exists():
        return fallback.resolve()
    raise FileNotFoundError(f"recorded artifact does not exist: {raw_path}")


def load_candidate_records(
    progress_paths: Iterable[Path] = DEFAULT_PROGRESS,
) -> tuple[list[dict[str, Any]], str]:
    pairs = candidate_benchmark.load_pairs()
    records: list[dict[str, Any]] = []
    candidate_paths: set[Path] = set()
    completed_timestamps: list[str] = []
    seen_run_keys: set[str] = set()

    for raw_progress_path in progress_paths:
        progress_path = raw_progress_path if raw_progress_path.is_absolute() else ROOT / raw_progress_path
        progress = load_json(progress_path)
        if progress.get("benchmark_version") != "0.4":
            raise ValueError(f"{progress_path}: expected benchmark version 0.4")
        for completed in progress.get("completed", []):
            condition = completed.get("condition")
            if condition not in GENERATOR_CONDITIONS:
                continue
            run_key = completed.get("run_key")
            if run_key in seen_run_keys:
                raise ValueError(f"duplicate generator run key: {run_key}")
            seen_run_keys.add(run_key)
            completed_timestamps.append(completed["completed_at_utc"])
            result_path = resolve_recorded_path(
                completed["result_file"], progress_path.parent
            )
            result = load_json(result_path)
            candidate_path = resolve_recorded_path(
                result["candidate_file"], progress_path.parent
            )
            if candidate_path in candidate_paths:
                raise ValueError(f"duplicate candidate artifact: {candidate_path}")
            candidate_paths.add(candidate_path)
            artifact = load_json(candidate_path)
            pair_id = completed["pair_id"]
            model_seed = completed["model_seed"]
            if (
                pair_id not in pairs
                or artifact.get("pair_id") != pair_id
                or artifact.get("generator") != condition
                or artifact.get("model_seed") != model_seed
            ):
                raise ValueError(f"{candidate_path}: metadata differs from progress ledger")
            candidates = artifact.get("candidates")
            if (
                not isinstance(candidates, list)
                or [item.get("id") for item in candidates]
                != candidate_benchmark.CANDIDATE_IDS
            ):
                raise ValueError(f"{candidate_path}: expected candidates C1-C8")
            variants = pairs[pair_id]
            auto_menu = candidate_benchmark.normalize_menu(candidates, artifact["matches"])
            auto_metrics = candidate_benchmark.candidate_metrics(
                variants, candidates, artifact["matches"], auto_menu
            )
            audit_candidate_results.assert_close(
                artifact["candidate_metrics"], auto_metrics, str(candidate_path)
            )
            records.append(
                {
                    "pair_id": pair_id,
                    "generator": condition,
                    "model_seed": model_seed,
                    "source_file": repo_relative(candidate_path),
                    "source_sha256": sha256_file(candidate_path),
                    "public_case": candidate_benchmark.public_generator_case(variants[0]),
                    "evidence_catalog": variants[0]["evidence_catalog"],
                    "candidates": candidates,
                    "auto_matches": artifact["matches"],
                    "auto_candidate_metrics": auto_metrics,
                }
            )

    expected_keys = {
        (pair_id, condition, seed)
        for pair_id in pairs
        for condition in GENERATOR_CONDITIONS
        for seed in (1, 2, 3)
    }
    actual_keys = {
        (record["pair_id"], record["generator"], record["model_seed"])
        for record in records
    }
    if actual_keys != expected_keys or len(records) != 48:
        raise ValueError("formal ledgers must resolve to exactly 48 registered candidate artifacts")
    return records, max(completed_timestamps)


def packet_without_hash(
    records: list[dict[str, Any]], source_completed_at: str, randomization_seed: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    shuffled = list(records)
    random.Random(randomization_seed).shuffle(shuffled)
    units: list[dict[str, Any]] = []
    key_units: list[dict[str, Any]] = []
    for index, record in enumerate(shuffled, start=1):
        review_id = f"R{index:03d}"
        units.append(
            {
                "review_id": review_id,
                "public_case": record["public_case"],
                "evidence_catalog": record["evidence_catalog"],
                "candidates": record["candidates"],
            }
        )
        key_units.append(
            {
                "review_id": review_id,
                "pair_id": record["pair_id"],
                "generator": record["generator"],
                "model_seed": record["model_seed"],
                "source_file": record["source_file"],
                "source_sha256": record["source_sha256"],
                "auto_matches": record["auto_matches"],
                "auto_candidate_metrics": record["auto_candidate_metrics"],
            }
        )
    packet = {
        "schema_version": "1.0",
        "benchmark_version": "0.4",
        "review_protocol": "condition-blind-candidate-mapping",
        "source_completed_at_utc": source_completed_at,
        "randomization_seed": randomization_seed,
        "review_unit_count": len(units),
        "candidate_row_count": sum(len(unit["candidates"]) for unit in units),
        "review_fields": {
            "mapped_evidence_id": "Choose exactly one catalog item that fully answers the candidate, otherwise NONE.",
            "atomic_single_observation": "1 only when the question requests one observation, comparison, or test.",
            "fully_answerable_by_mapping": "1 only when the selected single catalog item completely answers the question; must be 0 for NONE.",
            "distinct_from_other_candidates": "1 when the candidate is not a semantic duplicate of another question in the same set.",
            "action_discriminating": "1 when a plausible answer could change the relative support for at least two public actions.",
        },
        "review_units": units,
    }
    return packet, key_units


def add_packet_hash(packet: dict[str, Any]) -> dict[str, Any]:
    value = dict(packet)
    value["packet_hash"] = sha256_text(canonical_json(packet))
    return value


def assert_packet_blind(packet: dict[str, Any]) -> None:
    forbidden_keys = {
        "generator",
        "model_seed",
        "matches",
        "auto_matches",
        "candidate_metrics",
        "source_file",
        "source_sha256",
        "condition",
        "downstream",
        "oracle_facts",
        "variants",
    }

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in forbidden_keys:
                    raise ValueError(f"blind packet leaks forbidden key at {path}/{key}")
                walk(child, f"{path}/{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")

    walk(packet, "packet")


def packet_markdown(packet: dict[str, Any]) -> str:
    lines = [
        "# 候选问题—证据目录条件盲评审包",
        "",
        f"- Packet hash: `{packet['packet_hash']}`",
        f"- 评审单元：{packet['review_unit_count']}",
        f"- 候选问题：{packet['candidate_row_count']}",
        "",
        "## 评审规则",
        "",
        "对每道候选问题选择一个能够**完整回答**它的目录项；若没有单个目录项能够完整回答，填写 `NONE`。不要选择最接近但只能部分回答的目录项。",
        "",
        "四个质量字段只填 `0` 或 `1`：是否为单一原子观察、是否可由所选单项证据完整回答、是否不同于同组其他候选、是否可能区分至少两个公开行动。映射为 `NONE` 时，完整可答性必须为 `0`。",
        "",
    ]
    for unit in packet["review_units"]:
        case = unit["public_case"]
        lines.extend(
            [
                f"## {unit['review_id']} — {case['title']}",
                "",
                case["brief"],
                "",
                f"决策截止：{case['decision_deadline']}",
                "",
                "公开行动：",
                "",
                *[f"- {item['id']}: {item['label']}" for item in case["options"]],
                "",
                "可用数据能力：",
                "",
                *[
                    f"- {item['source']}: {', '.join(item['available_fields'])}"
                    for item in case["evidence_capabilities"]
                ],
                "",
                "证据目录：",
                "",
                *[
                    f"- {item['id']}: {item['question']}"
                    for item in unit["evidence_catalog"]
                ],
                "",
                "候选问题：",
                "",
                *[
                    f"- {item['id']}: {item['question']}"
                    for item in unit["candidates"]
                ],
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_csv(path: Path, fieldnames: Iterable[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def reviewer_bundle_readme(packet: dict[str, Any], reviewer_id: str) -> str:
    return f"""# 独立盲评材料

你的固定评审者 ID 是 `{reviewer_id}`，packet hash 是 `{packet['packet_hash']}`。

优先直接打开 `review-form.html`。页面完全离线，进度只保存在本机浏览器；完成后点击“导出 CSV”。请不要与另一位评审者讨论，也不要查阅项目仓库中的协调者目录、自动映射或实验结果。

如果不使用页面，可阅读 `blind-review-packet.md` 并填写 `reviewer-template.csv`。必须完成全部 384 行；映射为 `NONE` 时，“完整可答”填 `0`，映射为 E1-E6 时填 `1`。
"""


def write_reviewer_bundle(
    path: Path,
    packet: dict[str, Any],
    reviewer_id: str,
    packet_json: str,
    packet_md: str,
    form_html: str,
    form_csv: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = {
        "README.md": reviewer_bundle_readme(packet, reviewer_id),
        "review-form.html": form_html,
        "blind-review-packet.md": packet_md,
        "blind-review-packet.json": packet_json,
        "reviewer-template.csv": form_csv,
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 8, 20, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, content.encode("utf-8-sig") if name.endswith(".csv") else content.encode("utf-8"))


def review_template_rows(packet: dict[str, Any], reviewer_id: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for unit in packet["review_units"]:
        for candidate in unit["candidates"]:
            rows.append(
                {
                    "packet_hash": packet["packet_hash"],
                    "reviewer_id": reviewer_id,
                    "review_id": unit["review_id"],
                    "candidate_id": candidate["id"],
                    **{field: "" for field in REVIEW_FIELDS},
                    "notes": "",
                }
            )
    return rows


def review_form_html(packet: dict[str, Any]) -> str:
    embedded_packet = json.dumps(packet, ensure_ascii=False).replace("<", "\\u003c")
    template = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; font-src 'none'; connect-src 'none'">
  <title>候选问题条件盲评审</title>
  <style>
    :root { color-scheme: light dark; --bg: #f5f7fb; --panel: #fff; --ink: #172033; --muted: #65708a; --line: #d9dfeb; --accent: #3157d5; --soft: #eef2ff; --danger: #a43a3a; }
    @media (prefers-color-scheme: dark) { :root { --bg: #111522; --panel: #1a2030; --ink: #eef2ff; --muted: #aeb8cf; --line: #343e55; --accent: #8fa8ff; --soft: #232d49; --danger: #ff9b9b; } }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }
    header { position: sticky; top: 0; z-index: 5; background: color-mix(in srgb, var(--panel) 94%, transparent); border-bottom: 1px solid var(--line); backdrop-filter: blur(10px); }
    .toolbar, main { max-width: 1380px; margin: 0 auto; padding: 16px 24px; }
    .toolbar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
    h1 { font-size: 20px; margin: 0 auto 0 0; }
    h2 { font-size: 22px; margin: 0 0 8px; }
    h3 { font-size: 16px; margin: 0 0 8px; }
    p { margin: 6px 0; }
    button, input, select, textarea { font: inherit; color: inherit; }
    button { border: 1px solid var(--line); background: var(--panel); border-radius: 9px; padding: 8px 12px; cursor: pointer; }
    button.primary { color: white; background: var(--accent); border-color: var(--accent); }
    button:disabled { opacity: .45; cursor: not-allowed; }
    input, select, textarea { width: 100%; border: 1px solid var(--line); background: var(--panel); border-radius: 7px; padding: 7px 9px; }
    #reviewer-id { width: 160px; }
    .progress { min-width: 210px; color: var(--muted); }
    .progress-bar { height: 7px; margin-top: 4px; border-radius: 999px; background: var(--line); overflow: hidden; }
    .progress-bar > span { display: block; height: 100%; background: var(--accent); }
    .notice { background: var(--soft); border: 1px solid color-mix(in srgb, var(--accent) 28%, var(--line)); border-radius: 10px; padding: 12px 14px; margin-bottom: 16px; }
    .case-grid { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(320px, .9fr); gap: 16px; }
    .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 16px; }
    .catalog { margin: 0; padding-left: 22px; }
    .catalog li { margin: 7px 0; }
    .options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px 12px; margin-top: 12px; }
    .candidate-table { width: 100%; border-collapse: separate; border-spacing: 0; margin-top: 16px; background: var(--panel); border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
    th, td { border-bottom: 1px solid var(--line); border-right: 1px solid var(--line); padding: 10px; vertical-align: top; }
    th:last-child, td:last-child { border-right: 0; }
    tr:last-child td { border-bottom: 0; }
    th { text-align: left; font-size: 12px; color: var(--muted); background: color-mix(in srgb, var(--panel) 90%, var(--soft)); }
    td.question { min-width: 330px; }
    td.compact { width: 112px; }
    td.notes { min-width: 180px; }
    .candidate-id { display: inline-block; color: var(--accent); font-weight: 700; margin-right: 6px; }
    .invalid { outline: 2px solid var(--danger); }
    .nav { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin: 18px 0 40px; }
    .nav-center { display: flex; gap: 8px; align-items: center; }
    .muted { color: var(--muted); }
    .danger { color: var(--danger); }
    code { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
    @media (max-width: 900px) { .case-grid { grid-template-columns: 1fr; } .table-wrap { overflow-x: auto; } .toolbar { padding: 12px 14px; } main { padding: 14px; } }
  </style>
</head>
<body>
  <header>
    <div class="toolbar">
      <h1>候选问题条件盲评审</h1>
      <label>评审者 ID <input id="reviewer-id" autocomplete="off" placeholder="例如 reviewer-a"></label>
      <div class="progress"><span id="progress-text"></span><div class="progress-bar"><span id="progress-fill"></span></div></div>
      <button id="jump-incomplete">下一处未完成</button>
      <button class="primary" id="download">导出 CSV</button>
    </div>
  </header>
  <main>
    <div class="notice"><strong>独立盲评。</strong> 只判断一个目录项能否完整回答候选问题；不能完整回答就选 <code>NONE</code>。不要查看协调者密钥、自动映射、条件标签或实验成绩。所有内容只保存在本机浏览器，不发送网络请求。</div>
    <section id="case"></section>
    <div class="table-wrap"><table class="candidate-table"><thead><tr><th>候选问题</th><th>完整映射</th><th>原子问题</th><th>完整可答</th><th>同组独立</th><th>行动判别</th><th>说明（可选）</th></tr></thead><tbody id="candidate-body"></tbody></table></div>
    <div class="nav"><button id="previous">← 上一组</button><div class="nav-center"><span id="unit-position" class="muted"></span><select id="unit-select" style="width:110px"></select></div><button id="next">下一组 →</button></div>
  </main>
  <script id="packet-data" type="application/json">__PACKET_JSON__</script>
  <script>
    const packet = JSON.parse(document.getElementById('packet-data').textContent);
    const binaryFields = ['atomic_single_observation', 'fully_answerable_by_mapping', 'distinct_from_other_candidates', 'action_discriminating'];
    const allFields = ['mapped_evidence_id', ...binaryFields];
    const labels = { atomic_single_observation: '原子问题', fully_answerable_by_mapping: '完整可答', distinct_from_other_candidates: '同组独立', action_discriminating: '行动判别' };
    let current = 0;
    let answers = {};
    const reviewerInput = document.getElementById('reviewer-id');

    const keyFor = (reviewId, candidateId) => `${reviewId}|${candidateId}`;
    const storageKey = () => `blind-mapping-review:${packet.packet_hash}:${reviewerInput.value.trim() || 'unnamed'}`;
    const escapeHtml = value => String(value).replace(/[&<>"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[char]));
    const emptyAnswer = () => ({ mapped_evidence_id: '', atomic_single_observation: '', fully_answerable_by_mapping: '', distinct_from_other_candidates: '', action_discriminating: '', notes: '' });

    function loadProgress() {
      const reviewerId = reviewerInput.value.trim();
      if (!reviewerId) { answers = {}; render(); return; }
      try { answers = JSON.parse(localStorage.getItem(storageKey()) || '{}'); } catch { answers = {}; }
      render();
    }
    function saveProgress() {
      if (reviewerInput.value.trim()) localStorage.setItem(storageKey(), JSON.stringify(answers));
      updateProgress();
    }
    function rowComplete(answer) { return answer && allFields.every(field => answer[field] !== ''); }
    function updateProgress() {
      const total = packet.candidate_row_count;
      let complete = 0;
      packet.review_units.forEach(unit => unit.candidates.forEach(candidate => { if (rowComplete(answers[keyFor(unit.review_id, candidate.id)])) complete += 1; }));
      document.getElementById('progress-text').textContent = `${complete} / ${total} 已完成`;
      document.getElementById('progress-fill').style.width = `${100 * complete / total}%`;
    }
    function binarySelect(field, value) {
      return `<select data-field="${field}"><option value=""></option><option value="1" ${value === '1' ? 'selected' : ''}>1 是</option><option value="0" ${value === '0' ? 'selected' : ''}>0 否</option></select>`;
    }
    function render() {
      const unit = packet.review_units[current];
      const caseData = unit.public_case;
      document.getElementById('case').innerHTML = `<div class="case-grid"><div class="panel"><h2>${escapeHtml(unit.review_id)} · ${escapeHtml(caseData.title)}</h2><p>${escapeHtml(caseData.brief)}</p><p class="muted">决策截止：${escapeHtml(caseData.decision_deadline)}</p><div class="options">${caseData.options.map(item => `<div><strong>${escapeHtml(item.id)}</strong> ${escapeHtml(item.label)}</div>`).join('')}</div></div><div class="panel"><h3>证据目录</h3><ol class="catalog">${unit.evidence_catalog.map(item => `<li><strong>${escapeHtml(item.id)}</strong> ${escapeHtml(item.question)}</li>`).join('')}</ol></div></div>`;
      const body = document.getElementById('candidate-body');
      body.innerHTML = unit.candidates.map(candidate => {
        const key = keyFor(unit.review_id, candidate.id);
        const answer = answers[key] || emptyAnswer();
        const mappingOptions = ['','E1','E2','E3','E4','E5','E6','NONE'].map(value => `<option value="${value}" ${answer.mapped_evidence_id === value ? 'selected' : ''}>${value}</option>`).join('');
        return `<tr data-key="${key}"><td class="question"><span class="candidate-id">${escapeHtml(candidate.id)}</span>${escapeHtml(candidate.question)}</td><td class="compact"><select data-field="mapped_evidence_id">${mappingOptions}</select></td>${binaryFields.map(field => `<td class="compact" title="${labels[field]}">${binarySelect(field, answer[field])}</td>`).join('')}<td class="notes"><textarea rows="3" data-field="notes">${escapeHtml(answer.notes || '')}</textarea></td></tr>`;
      }).join('');
      body.querySelectorAll('select, textarea').forEach(control => control.addEventListener('change', event => {
        const row = event.target.closest('tr');
        const key = row.dataset.key;
        answers[key] = answers[key] || emptyAnswer();
        answers[key][event.target.dataset.field] = event.target.value;
        validateRow(row, answers[key]);
        saveProgress();
      }));
      body.querySelectorAll('tr').forEach(row => validateRow(row, answers[row.dataset.key] || emptyAnswer()));
      document.getElementById('previous').disabled = current === 0;
      document.getElementById('next').disabled = current === packet.review_units.length - 1;
      document.getElementById('unit-position').textContent = `第 ${current + 1} / ${packet.review_units.length} 组`;
      document.getElementById('unit-select').value = String(current);
      updateProgress();
      window.scrollTo({top: 0, behavior: 'smooth'});
    }
    function validateRow(row, answer) {
      row.classList.remove('invalid');
      if (!answer.mapped_evidence_id || answer.fully_answerable_by_mapping === '') return;
      const consistent = (answer.mapped_evidence_id === 'NONE') === (answer.fully_answerable_by_mapping === '0');
      if (!consistent) row.classList.add('invalid');
    }
    function go(index) { current = Math.max(0, Math.min(packet.review_units.length - 1, index)); render(); }
    function jumpIncomplete() {
      for (let offset = 1; offset <= packet.review_units.length; offset += 1) {
        const index = (current + offset) % packet.review_units.length;
        const unit = packet.review_units[index];
        if (unit.candidates.some(candidate => !rowComplete(answers[keyFor(unit.review_id, candidate.id)]))) { go(index); return; }
      }
      alert('全部候选都已填写。请导出 CSV 并交给协调者校验。');
    }
    function csvCell(value) { const text = String(value ?? ''); return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text; }
    function downloadCsv() {
      const reviewerId = reviewerInput.value.trim();
      if (!reviewerId) { alert('请先填写唯一的评审者 ID。'); reviewerInput.focus(); return; }
      const columns = ['packet_hash','reviewer_id','review_id','candidate_id','mapped_evidence_id','atomic_single_observation','fully_answerable_by_mapping','distinct_from_other_candidates','action_discriminating','notes'];
      const rows = [columns];
      packet.review_units.forEach(unit => unit.candidates.forEach(candidate => {
        const answer = answers[keyFor(unit.review_id, candidate.id)] || emptyAnswer();
        rows.push([packet.packet_hash, reviewerId, unit.review_id, candidate.id, ...allFields.map(field => answer[field] || ''), answer.notes || '']);
      }));
      const csvText = '\ufeff' + rows.map(row => row.map(csvCell).join(',')).join('\r\n') + '\r\n';
      const blob = new Blob([csvText], {type: 'text/csv;charset=utf-8'});
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `${reviewerId}-${packet.packet_hash.slice(0, 8)}.csv`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    }

    reviewerInput.value = localStorage.getItem(`blind-mapping-reviewer:${packet.packet_hash}`) || '';
    reviewerInput.addEventListener('change', () => { localStorage.setItem(`blind-mapping-reviewer:${packet.packet_hash}`, reviewerInput.value.trim()); loadProgress(); });
    document.getElementById('previous').addEventListener('click', () => go(current - 1));
    document.getElementById('next').addEventListener('click', () => go(current + 1));
    document.getElementById('jump-incomplete').addEventListener('click', jumpIncomplete);
    document.getElementById('download').addEventListener('click', downloadCsv);
    document.getElementById('unit-select').innerHTML = packet.review_units.map((unit, index) => `<option value="${index}">${unit.review_id}</option>`).join('');
    document.getElementById('unit-select').addEventListener('change', event => go(Number(event.target.value)));
    loadProgress();
  </script>
</body>
</html>
"""
    return template.replace("__PACKET_JSON__", embedded_packet)


def package_readme(packet: dict[str, Any]) -> str:
    return f"""# Blind Mapping Review v0.4

本目录是 Candidate Generation v0.4 的条件盲人工映射复核材料，不调用模型 API。

## 给评审者

每位评审者只接收：

1. 最安全的分发方式是对应的 `reviewer-bundles/reviewer-*.zip`；
2. 解压后直接打开 `review-form.html`，逐组填写并导出 CSV；
3. 或使用 Markdown 评审包与 `reviewer-template.csv`。

不要打开 `coordinator/unblinding-key.json`，也不要查看原实验候选工件、自动映射、生成条件、模型种子、隐藏事实或下游结果。两位评审者独立完成全部 {packet['candidate_row_count']} 行，在提交前不讨论答案。

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

Packet hash: `{packet['packet_hash']}`
"""


def build_package(
    output_dir: Path,
    progress_paths: Iterable[Path] = DEFAULT_PROGRESS,
    randomization_seed: int = RANDOMIZATION_SEED,
) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty review directory: {output_dir}")
    records, completed_at = load_candidate_records(progress_paths)
    packet_base, key_units = packet_without_hash(
        records, completed_at, randomization_seed
    )
    packet = add_packet_hash(packet_base)
    assert_packet_blind(packet)
    output_dir.mkdir(parents=True, exist_ok=True)
    packet_dir = output_dir / "packet"
    forms_dir = output_dir / "forms"
    coordinator_dir = output_dir / "coordinator"
    bundles_dir = output_dir / "reviewer-bundles"
    packet_dir.mkdir(exist_ok=True)
    forms_dir.mkdir(exist_ok=True)
    coordinator_dir.mkdir(exist_ok=True)
    bundles_dir.mkdir(exist_ok=True)
    packet_json = json.dumps(packet, ensure_ascii=False, indent=2) + "\n"
    packet_md = packet_markdown(packet)
    form_html = review_form_html(packet)
    (packet_dir / "blind-review-packet.json").write_text(packet_json, encoding="utf-8")
    (packet_dir / "blind-review-packet.md").write_text(packet_md, encoding="utf-8")
    (packet_dir / "review-form.html").write_text(
        form_html, encoding="utf-8"
    )
    for reviewer_id in ("reviewer-1", "reviewer-2"):
        form_path = forms_dir / f"{reviewer_id}.csv"
        write_csv(
            form_path,
            REVIEW_COLUMNS,
            review_template_rows(packet, reviewer_id),
        )
        write_reviewer_bundle(
            bundles_dir / f"{reviewer_id}.zip",
            packet,
            reviewer_id,
            packet_json,
            packet_md,
            form_html,
            form_path.read_text(encoding="utf-8-sig"),
        )
    key = {
        "schema_version": "1.0",
        "packet_hash": packet["packet_hash"],
        "warning": "Coordinator only. Do not provide this file to reviewers before both reviews are locked.",
        "review_units": key_units,
    }
    (coordinator_dir / "unblinding-key.json").write_text(
        json.dumps(key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "README.md").write_text(package_readme(packet), encoding="utf-8")
    return {
        "status": "built",
        "packet_hash": packet["packet_hash"],
        "review_units": packet["review_unit_count"],
        "candidate_rows": packet["candidate_row_count"],
        "output_dir": str(output_dir),
    }


def load_packet(path: Path) -> dict[str, Any]:
    packet = load_json(path)
    expected_hash = packet.pop("packet_hash", None)
    actual_hash = sha256_text(canonical_json(packet))
    packet["packet_hash"] = expected_hash
    if expected_hash != actual_hash:
        raise ValueError(f"{path}: packet hash mismatch")
    assert_packet_blind(packet)
    return packet


def packet_row_keys(packet: dict[str, Any]) -> dict[tuple[str, str], set[str]]:
    return {
        (unit["review_id"], candidate["id"]): {
            item["id"] for item in unit["evidence_catalog"]
        }
        for unit in packet["review_units"]
        for candidate in unit["candidates"]
    }


def load_review(
    path: Path, packet: dict[str, Any]
) -> tuple[str, dict[tuple[str, str], dict[str, str]]]:
    expected = packet_row_keys(packet)
    rows: dict[tuple[str, str], dict[str, str]] = {}
    reviewer_ids: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != REVIEW_COLUMNS:
            raise ValueError(f"{path}: review columns differ from template")
        for row_number, raw in enumerate(reader, start=2):
            if raw["packet_hash"] != packet["packet_hash"]:
                raise ValueError(f"{path}:{row_number}: packet hash differs")
            reviewer_id = raw["reviewer_id"].strip()
            if not reviewer_id:
                raise ValueError(f"{path}:{row_number}: reviewer_id is required")
            reviewer_ids.add(reviewer_id)
            key = (raw["review_id"].strip(), raw["candidate_id"].strip())
            if key not in expected or key in rows:
                raise ValueError(f"{path}:{row_number}: unexpected or duplicate row {key}")
            mapping = raw["mapped_evidence_id"].strip().upper()
            allowed_mapping = expected[key] | {"NONE"}
            if mapping not in allowed_mapping:
                raise ValueError(f"{path}:{row_number}: invalid mapping {mapping!r}")
            row = {
                "mapped_evidence_id": mapping,
                "notes": raw.get("notes", "").strip(),
            }
            for field in BINARY_FIELDS:
                value = raw[field].strip()
                if value not in {"0", "1"}:
                    raise ValueError(f"{path}:{row_number}: {field} must be 0 or 1")
                row[field] = value
            if (mapping == "NONE") != (row["fully_answerable_by_mapping"] == "0"):
                raise ValueError(
                    f"{path}:{row_number}: NONE must pair with fully_answerable_by_mapping=0, and E1-E6 with 1"
                )
            rows[key] = row
    if set(rows) != set(expected):
        raise ValueError(f"{path}: review must contain all {len(expected)} candidate rows")
    if len(reviewer_ids) != 1:
        raise ValueError(f"{path}: expected exactly one reviewer_id")
    return next(iter(reviewer_ids)), rows


def disagreement_rows(
    packet: dict[str, Any],
    reviewer_one_id: str,
    review_one: dict[tuple[str, str], dict[str, str]],
    reviewer_two_id: str,
    review_two: dict[tuple[str, str], dict[str, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for review_id, candidate_id in sorted(review_one):
        for field in REVIEW_FIELDS:
            left = review_one[(review_id, candidate_id)][field]
            right = review_two[(review_id, candidate_id)][field]
            if left == right:
                continue
            rows.append(
                {
                    "packet_hash": packet["packet_hash"],
                    "review_id": review_id,
                    "candidate_id": candidate_id,
                    "field": field,
                    "reviewer_one_id": reviewer_one_id,
                    "reviewer_one_value": left,
                    "reviewer_two_id": reviewer_two_id,
                    "reviewer_two_value": right,
                    "final_value": "",
                    "adjudicator_id": "",
                    "notes": "",
                }
            )
    return rows


def prepare_adjudication(
    packet_path: Path, reviewer_one_path: Path, reviewer_two_path: Path, output_path: Path
) -> dict[str, Any]:
    packet = load_packet(packet_path)
    reviewer_one_id, review_one = load_review(reviewer_one_path, packet)
    reviewer_two_id, review_two = load_review(reviewer_two_path, packet)
    if reviewer_one_id == reviewer_two_id:
        raise ValueError("reviewer IDs must be different")
    rows = disagreement_rows(
        packet, reviewer_one_id, review_one, reviewer_two_id, review_two
    )
    write_csv(output_path, ADJUDICATION_COLUMNS, rows)
    return {
        "status": "prepared",
        "reviewer_one": reviewer_one_id,
        "reviewer_two": reviewer_two_id,
        "disagreements": len(rows),
        "output": str(output_path),
    }


def valid_final_value(field: str, value: str, evidence_ids: set[str]) -> bool:
    if field == "mapped_evidence_id":
        return value in evidence_ids | {"NONE"}
    return field in BINARY_FIELDS and value in {"0", "1"}


def load_adjudication(
    path: Path,
    packet: dict[str, Any],
    expected_rows: list[dict[str, str]],
) -> dict[tuple[str, str, str], str]:
    catalog_by_row = packet_row_keys(packet)
    expected = {
        (row["review_id"], row["candidate_id"], row["field"]): row
        for row in expected_rows
    }
    resolved: dict[tuple[str, str, str], str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != ADJUDICATION_COLUMNS:
            raise ValueError(f"{path}: adjudication columns differ from template")
        for row_number, raw in enumerate(reader, start=2):
            if raw["packet_hash"] != packet["packet_hash"]:
                raise ValueError(f"{path}:{row_number}: packet hash differs")
            key = (
                raw["review_id"].strip(),
                raw["candidate_id"].strip(),
                raw["field"].strip(),
            )
            if key not in expected or key in resolved:
                raise ValueError(f"{path}:{row_number}: unexpected or duplicate adjudication {key}")
            expected_row = expected[key]
            for column in (
                "reviewer_one_id",
                "reviewer_one_value",
                "reviewer_two_id",
                "reviewer_two_value",
            ):
                if raw[column].strip() != expected_row[column]:
                    raise ValueError(f"{path}:{row_number}: {column} differs from locked reviews")
            if not raw["adjudicator_id"].strip():
                raise ValueError(f"{path}:{row_number}: adjudicator_id is required")
            value = raw["final_value"].strip().upper()
            evidence_ids = catalog_by_row[key[:2]]
            if not valid_final_value(key[2], value, evidence_ids):
                raise ValueError(f"{path}:{row_number}: invalid final_value {value!r}")
            resolved[key] = value
    if set(resolved) != set(expected):
        raise ValueError(f"{path}: every disagreement must be adjudicated")
    return resolved


def consensus_review(
    packet: dict[str, Any],
    reviewer_one_id: str,
    review_one: dict[tuple[str, str], dict[str, str]],
    reviewer_two_id: str,
    review_two: dict[tuple[str, str], dict[str, str]],
    adjudication_path: Path,
) -> dict[tuple[str, str], dict[str, str]]:
    disagreements = disagreement_rows(
        packet, reviewer_one_id, review_one, reviewer_two_id, review_two
    )
    resolved = load_adjudication(adjudication_path, packet, disagreements)
    consensus: dict[tuple[str, str], dict[str, str]] = {}
    for key in review_one:
        row: dict[str, str] = {}
        for field in REVIEW_FIELDS:
            left = review_one[key][field]
            right = review_two[key][field]
            row[field] = left if left == right else resolved[(*key, field)]
        if (row["mapped_evidence_id"] == "NONE") != (
            row["fully_answerable_by_mapping"] == "0"
        ):
            raise ValueError(f"adjudicated row {key} has inconsistent mapping/answerability")
        consensus[key] = row
    return consensus


def cohen_kappa(left: list[str], right: list[str]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("kappa inputs must be non-empty and equally sized")
    observed = sum(a == b for a, b in zip(left, right)) / len(left)
    left_counts = Counter(left)
    right_counts = Counter(right)
    labels = set(left_counts) | set(right_counts)
    expected = sum(
        (left_counts[label] / len(left)) * (right_counts[label] / len(right))
        for label in labels
    )
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)


def agreement_summary(
    review_one: dict[tuple[str, str], dict[str, str]],
    review_two: dict[tuple[str, str], dict[str, str]],
) -> dict[str, Any]:
    keys = sorted(review_one)
    summary: dict[str, Any] = {}
    for field in REVIEW_FIELDS:
        left = [review_one[key][field] for key in keys]
        right = [review_two[key][field] for key in keys]
        summary[field] = {
            "exact_agreement_rate": sum(a == b for a, b in zip(left, right)) / len(keys),
            "cohen_kappa": cohen_kappa(left, right),
            "disagreement_count": sum(a != b for a, b in zip(left, right)),
        }
    return summary


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def summarize_metric_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "candidate_sets": len(rows),
        "both_branches_full_critical_coverage_count": sum(
            item["both_branches_full_critical_coverage"] for item in rows
        ),
        "minimum_branch_critical_coverage": mean(
            [item["minimum_branch_critical_coverage"] for item in rows]
        ),
        "catalog_match_rate": mean([item["catalog_match_rate"] for item in rows]),
        "unique_evidence_count": mean([item["unique_evidence_count"] for item in rows]),
        "duplicate_match_count": mean([item["duplicate_match_count"] for item in rows]),
        "normalized_menu_count": mean([item["normalized_menu_count"] for item in rows]),
    }


def candidate_gates(
    human_by_condition: dict[str, dict[str, Any]], baseline_gates: dict[str, Any]
) -> dict[str, dict[str, bool]]:
    gates: dict[str, dict[str, bool]] = {}
    g0 = human_by_condition["G0"]
    g0_values = dict(baseline_gates["G0"])
    g0_values.pop("passed", None)
    g0_values.update(
        {
            "full_critical_coverage_at_least_8": (
                g0["both_branches_full_critical_coverage_count"] >= 8
            ),
            "catalog_match_rate_at_least_0_60": g0["catalog_match_rate"] >= 0.60,
            "unique_evidence_at_least_3_5": g0["unique_evidence_count"] >= 3.5,
        }
    )
    gates["G0"] = {**g0_values, "passed": all(g0_values.values())}
    for condition in ("GQ", "GS", "GB"):
        current = human_by_condition[condition]
        values = dict(baseline_gates[condition])
        values.pop("passed", None)
        if g0["both_branches_full_critical_coverage_count"] >= 10:
            coverage_passed = (
                current["both_branches_full_critical_coverage_count"]
                >= g0["both_branches_full_critical_coverage_count"]
            )
        else:
            coverage_passed = (
                current["both_branches_full_critical_coverage_count"]
                >= g0["both_branches_full_critical_coverage_count"] + 2
            )
        values.update(
            {
                "full_critical_coverage_increment": coverage_passed,
                "minimum_critical_coverage_not_below_G0": (
                    current["minimum_branch_critical_coverage"]
                    >= g0["minimum_branch_critical_coverage"]
                ),
            }
        )
        gates[condition] = {**values, "passed": all(values.values())}
    return gates


def sensitivity_analysis(
    packet: dict[str, Any],
    key: dict[str, Any],
    reviewer_one_id: str,
    review_one: dict[tuple[str, str], dict[str, str]],
    reviewer_two_id: str,
    review_two: dict[tuple[str, str], dict[str, str]],
    consensus: dict[tuple[str, str], dict[str, str]],
    progress_paths: Iterable[Path] = DEFAULT_PROGRESS,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if key.get("packet_hash") != packet["packet_hash"]:
        raise ValueError("unblinding key does not match packet")
    key_by_id = {item["review_id"]: item for item in key.get("review_units", [])}
    if set(key_by_id) != {unit["review_id"] for unit in packet["review_units"]}:
        raise ValueError("unblinding key units differ from packet")
    pairs = candidate_benchmark.load_pairs()
    auto_metrics_by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    human_metrics_by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    quality_by_condition: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )
    mapping_disagreements_by_condition: Counter[str] = Counter()
    mapping_rows_by_condition: Counter[str] = Counter()
    consensus_export: list[dict[str, Any]] = []

    for unit in packet["review_units"]:
        review_id = unit["review_id"]
        unblinded = key_by_id[review_id]
        condition = unblinded["generator"]
        matches = {
            candidate["id"]: consensus[(review_id, candidate["id"])][
                "mapped_evidence_id"
            ]
            for candidate in unit["candidates"]
        }
        menu = candidate_benchmark.normalize_menu(unit["candidates"], matches)
        human_metrics = candidate_benchmark.candidate_metrics(
            pairs[unblinded["pair_id"]], unit["candidates"], matches, menu
        )
        auto_menu = candidate_benchmark.normalize_menu(
            unit["candidates"], unblinded["auto_matches"]
        )
        auto_metrics = candidate_benchmark.candidate_metrics(
            pairs[unblinded["pair_id"]],
            unit["candidates"],
            unblinded["auto_matches"],
            auto_menu,
        )
        audit_candidate_results.assert_close(
            unblinded["auto_candidate_metrics"], auto_metrics, review_id
        )
        auto_metrics_by_condition[condition].append(auto_metrics)
        human_metrics_by_condition[condition].append(human_metrics)
        for candidate in unit["candidates"]:
            candidate_id = candidate["id"]
            row = consensus[(review_id, candidate_id)]
            auto_mapping = unblinded["auto_matches"][candidate_id]
            mapping_rows_by_condition[condition] += 1
            if row["mapped_evidence_id"] != auto_mapping:
                mapping_disagreements_by_condition[condition] += 1
            for field in BINARY_FIELDS:
                quality_by_condition[condition][field].append(int(row[field]))
            consensus_export.append(
                {
                    "review_id": review_id,
                    "pair_id": unblinded["pair_id"],
                    "generator": condition,
                    "model_seed": unblinded["model_seed"],
                    "candidate_id": candidate_id,
                    "auto_mapping": auto_mapping,
                    **{field: row[field] for field in REVIEW_FIELDS},
                }
            )

    auto_by_condition = {
        condition: summarize_metric_rows(auto_metrics_by_condition[condition])
        for condition in GENERATOR_CONDITIONS
    }
    human_by_condition = {
        condition: summarize_metric_rows(human_metrics_by_condition[condition])
        for condition in GENERATOR_CONDITIONS
    }
    condition_comparison: dict[str, Any] = {}
    for condition in GENERATOR_CONDITIONS:
        condition_comparison[condition] = {
            "auto": auto_by_condition[condition],
            "human_consensus": human_by_condition[condition],
            "delta_human_minus_auto": {
                field: human_by_condition[condition][field] - auto_by_condition[condition][field]
                for field in (
                    "both_branches_full_critical_coverage_count",
                    "minimum_branch_critical_coverage",
                    "catalog_match_rate",
                    "unique_evidence_count",
                    "duplicate_match_count",
                    "normalized_menu_count",
                )
            },
            "auto_vs_human_mapping_disagreement_count": mapping_disagreements_by_condition[
                condition
            ],
            "auto_vs_human_mapping_disagreement_rate": (
                mapping_disagreements_by_condition[condition]
                / mapping_rows_by_condition[condition]
            ),
            "human_quality_rates": {
                field: mean(quality_by_condition[condition][field])
                for field in BINARY_FIELDS
            },
        }

    formal_audit = audit_candidate_results.audit(list(progress_paths))
    human_gates = candidate_gates(human_by_condition, formal_audit["gates"])
    gate_flips: list[dict[str, Any]] = []
    for condition in GENERATOR_CONDITIONS:
        for gate, human_value in human_gates[condition].items():
            auto_value = formal_audit["gates"][condition][gate]
            if human_value != auto_value:
                gate_flips.append(
                    {
                        "condition": condition,
                        "gate": gate,
                        "auto": auto_value,
                        "human_consensus": human_value,
                    }
                )

    total_mapping_rows = sum(mapping_rows_by_condition.values())
    total_mapping_disagreements = sum(mapping_disagreements_by_condition.values())
    auto_human_disagreement_rate = total_mapping_disagreements / total_mapping_rows
    agreement = agreement_summary(review_one, review_two)
    mapping_agreement = agreement["mapped_evidence_id"]
    reliability_warning = (
        mapping_agreement["exact_agreement_rate"]
        < MIN_REVIEWER_EXACT_MAPPING_AGREEMENT
        or mapping_agreement["cohen_kappa"] < MIN_REVIEWER_MAPPING_KAPPA
    )
    material_mapping_issue = (
        auto_human_disagreement_rate >= MATERIAL_MAPPING_DISAGREEMENT_RATE
        or bool(gate_flips)
    )
    if reliability_warning:
        recommendation = "repeat_rubric_calibration_before_interpreting_sensitivity"
    elif material_mapping_issue:
        recommendation = "fix_mapping_interface_before_gq2"
    else:
        recommendation = "proceed_to_gq2_generator_development"

    result = {
        "status": "complete",
        "packet_hash": packet["packet_hash"],
        "reviewers": [reviewer_one_id, reviewer_two_id],
        "reviewer_agreement": agreement,
        "auto_vs_human_consensus": {
            "mapping_rows": total_mapping_rows,
            "mapping_disagreement_count": total_mapping_disagreements,
            "mapping_disagreement_rate": auto_human_disagreement_rate,
            "material_rate_threshold": MATERIAL_MAPPING_DISAGREEMENT_RATE,
        },
        "condition_comparison": condition_comparison,
        "auto_gates": formal_audit["gates"],
        "human_consensus_gates": human_gates,
        "gate_flips": gate_flips,
        "decision": {
            "reviewer_reliability_warning": reliability_warning,
            "material_mapping_issue": material_mapping_issue,
            "recommendation": recommendation,
        },
    }
    return result, consensus_export


def sensitivity_report(result: dict[str, Any]) -> str:
    agreement = result["reviewer_agreement"]["mapped_evidence_id"]
    auto_human = result["auto_vs_human_consensus"]
    lines = [
        "# Candidate Generation v0.4：条件盲人工映射敏感性报告",
        "",
        f"Packet hash: `{result['packet_hash']}`",
        "",
        "## 决策结论",
        "",
        f"- 建议：`{result['decision']['recommendation']}`",
        f"- 两位评审者映射完全一致率：`{agreement['exact_agreement_rate']:.3f}`；Cohen's kappa：`{agreement['cohen_kappa']:.3f}`。",
        f"- 自动映射与人工共识不一致率：`{auto_human['mapping_disagreement_rate']:.3f}`（{auto_human['mapping_disagreement_count']}/{auto_human['mapping_rows']}）。",
        f"- 推进门槛翻转数：`{len(result['gate_flips'])}`。",
        "",
        "## 分条件候选指标",
        "",
        "| 条件 | 自动全覆盖 | 人工全覆盖 | 自动最低覆盖 | 人工最低覆盖 | 自动匹配率 | 人工匹配率 | 映射不一致率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for condition in GENERATOR_CONDITIONS:
        comparison = result["condition_comparison"][condition]
        auto = comparison["auto"]
        human = comparison["human_consensus"]
        lines.append(
            "| {condition} | {auto_full:.0f}/12 | {human_full:.0f}/12 | {auto_min:.3f} | {human_min:.3f} | {auto_match:.3f} | {human_match:.3f} | {disagreement:.3f} |".format(
                condition=condition,
                auto_full=auto["both_branches_full_critical_coverage_count"],
                human_full=human["both_branches_full_critical_coverage_count"],
                auto_min=auto["minimum_branch_critical_coverage"],
                human_min=human["minimum_branch_critical_coverage"],
                auto_match=auto["catalog_match_rate"],
                human_match=human["catalog_match_rate"],
                disagreement=comparison["auto_vs_human_mapping_disagreement_rate"],
            )
        )
    lines.extend(
        [
            "",
            "## 门槛敏感性",
            "",
        ]
    )
    if result["gate_flips"]:
        lines.extend(
            f"- {item['condition']} / `{item['gate']}`：自动 `{item['auto']}` → 人工 `{item['human_consensus']}`"
            for item in result["gate_flips"]
        )
    else:
        lines.append("- 人工共识没有改变任何预注册推进门槛判定。")
    lines.extend(
        [
            "",
            "## 解释规则",
            "",
            "- 若评审者映射一致率低于 0.85 或 kappa 低于 0.70，先校准评分规范，再解释敏感性结果。",
            "- 在评审可靠的前提下，若自动—人工映射不一致率至少为 0.10，或任一推进门槛翻转，先修复匹配接口。",
            "- 只有评审可靠、差异低于实质阈值且没有门槛翻转时，才把主要瓶颈归于生成器并进入 GQ2。",
            "",
        ]
    )
    return "\n".join(lines)


def write_analysis(
    output_dir: Path, result: dict[str, Any], consensus_rows: list[dict[str, Any]]
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "sensitivity-results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "SENSITIVITY-REPORT.md").write_text(
        sensitivity_report(result), encoding="utf-8"
    )
    consensus_columns = (
        "review_id",
        "pair_id",
        "generator",
        "model_seed",
        "candidate_id",
        "auto_mapping",
        *REVIEW_FIELDS,
    )
    write_csv(output_dir / "consensus-mappings.csv", consensus_columns, consensus_rows)


def default_packet_path() -> Path:
    return DEFAULT_OUTPUT / "packet" / "blind-review-packet.json"


def default_key_path() -> Path:
    return DEFAULT_OUTPUT / "coordinator" / "unblinding-key.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build and analyze condition-blind candidate mapping reviews"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    build_parser.add_argument("--randomization-seed", type=int, default=RANDOMIZATION_SEED)
    build_parser.add_argument("progress", type=Path, nargs="*", default=DEFAULT_PROGRESS)

    validate_parser = subparsers.add_parser("validate-review")
    validate_parser.add_argument("review", type=Path)
    validate_parser.add_argument("--packet", type=Path, default=default_packet_path())

    prepare_parser = subparsers.add_parser("prepare-adjudication")
    prepare_parser.add_argument("--packet", type=Path, default=default_packet_path())
    prepare_parser.add_argument("--reviewer-one", type=Path, required=True)
    prepare_parser.add_argument("--reviewer-two", type=Path, required=True)
    prepare_parser.add_argument("--output", type=Path, required=True)

    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("--packet", type=Path, default=default_packet_path())
    analyze_parser.add_argument("--key", type=Path, default=default_key_path())
    analyze_parser.add_argument("--reviewer-one", type=Path, required=True)
    analyze_parser.add_argument("--reviewer-two", type=Path, required=True)
    analyze_parser.add_argument("--adjudication", type=Path, required=True)
    analyze_parser.add_argument("--output", type=Path, required=True)
    analyze_parser.add_argument("progress", type=Path, nargs="*", default=DEFAULT_PROGRESS)

    args = parser.parse_args()
    if args.command == "build":
        result = build_package(args.output, args.progress, args.randomization_seed)
    elif args.command == "validate-review":
        packet = load_packet(args.packet)
        reviewer_id, rows = load_review(args.review, packet)
        result = {"status": "valid", "reviewer_id": reviewer_id, "rows": len(rows)}
    elif args.command == "prepare-adjudication":
        result = prepare_adjudication(
            args.packet, args.reviewer_one, args.reviewer_two, args.output
        )
    else:
        packet = load_packet(args.packet)
        key = load_json(args.key)
        reviewer_one_id, review_one = load_review(args.reviewer_one, packet)
        reviewer_two_id, review_two = load_review(args.reviewer_two, packet)
        if reviewer_one_id == reviewer_two_id:
            raise ValueError("reviewer IDs must be different")
        consensus = consensus_review(
            packet,
            reviewer_one_id,
            review_one,
            reviewer_two_id,
            review_two,
            args.adjudication,
        )
        result, consensus_rows = sensitivity_analysis(
            packet,
            key,
            reviewer_one_id,
            review_one,
            reviewer_two_id,
            review_two,
            consensus,
            args.progress,
        )
        write_analysis(args.output, result, consensus_rows)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
