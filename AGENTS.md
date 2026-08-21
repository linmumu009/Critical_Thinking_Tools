# Repository Instructions

## Versioning and delivery

- Every completed project update must be committed to Git.
- Before committing an update, add a corresponding entry to the `README.md` section `版本更新记录`.
- Use semantic versioning (`MAJOR.MINOR.PATCH`) and include the release date in each version heading.
- Keep each commit focused on the completed update and use a concise, descriptive commit message.
- Push completed commits to the configured `origin` remote unless the user explicitly asks not to push or pushing is blocked by authentication/network access.
- Do not rewrite or discard unrelated user changes while preparing a commit.

## Research-question modes

- When the user starts a new research-question discovery run, ask them to choose mode 1 or mode 2 before execution unless they already specified the mode.
- Mode 1 and mode 2 execute the same research-question discovery pipeline. Mode 1 uses the external-model API engine; mode 2 uses the current Codex engine. They are engine choices, not different methods.
- Before executing either mode, read `modes/codex-research-question/PROTOCOL.md`, `research-profile.json`, and `pipeline-stages.json` completely, plus the adapter prompt for the selected mode.
- Do not add, remove, merge, reorder, or reinterpret stages, required thinking tools, candidate schema, hard gates, scorecard, cheap-probe rules, or output contract for only one mode. A process change must update the shared pipeline and apply to both engines.
- The shared pipeline is the repository's Question Discovery Funnel: goal, reality signals, 5W1H/Socratic reframing, QFT, STORM, mechanism/evidence clustering, bidirectional steelman and competing hypotheses, scorecard ranking, cheap reality probes, and the final question contract.
- Mode 2 must not read API credentials or call the user's configured external model API. Engine-specific prompts may adapt execution mechanics to Codex, but cannot replace the shared workflow with an autonomous Codex-only workflow.
- Neither mode requires the user to score candidates. Ask the user only when a missing real-world constraint or authorization would materially change the direction.
- Candidate-mapping audit outputs from `benchmarks/question-discovery-v0.1/` are benchmark diagnostics, not research evidence for either mode.
- Save completed research artifacts in `modes/codex-research-question/results/`, validate them with the local session validator, and include them in the versioned Git update.
- Sessions with schema version 1.0 in the results directory are historical autonomous-workflow artifacts and must not be described as completed mode 2 runs.
