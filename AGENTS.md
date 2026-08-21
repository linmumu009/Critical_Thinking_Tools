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
- Mode 1 is the existing external-model API experimental pipeline under `benchmarks/question-discovery-v0.1/`.
- Mode 2 has exactly one business goal: discover a strong research question for LLM text post-training, synthetic data, GRPO/RLVR, and adjacent areas.
- Before executing mode 2, read `modes/codex-research-question/PROTOCOL.md` and `research-profile.json` completely.
- Mode 2 is executed by the current Codex. It must not read API credentials, call the user's configured external model API, reuse candidate-mapping audit results as research evidence, or require the user to score candidates.
- Mode 2 should work autonomously from current primary sources and the fixed profile. Ask the user only when a missing constraint would materially change the research direction.
- Save completed mode 2 research artifacts in `modes/codex-research-question/results/`, validate them with the local session validator, and include them in the versioned Git update.
