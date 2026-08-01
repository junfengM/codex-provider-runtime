# Repository agent instructions

## Completion and publishing

- Treat the private GitHub repository's default `main` branch as the source of truth.
- For every completed code, documentation, skill, or compatibility change: work on an
  `agent/*` branch, run the relevant checks, open a pull request, merge it, and update
  the local `main` branch from `origin/main`.
- Do not report a change as complete while it exists only in a local commit, remote
  feature branch, Draft PR, or open PR.
- Before final handoff, verify that the GitHub default branch is `main`, the intended
  PR is merged, and local `main` and `origin/main` resolve to the same commit.
- If publishing or merging is blocked by credentials, policy, CI, or merge conflicts,
  report that exact blocker instead of describing the work as complete.

## Compatibility knowledge

- Keep the runtime, tests, documentation, and bundled Codex skills synchronized when
  new Codex or DeepSeek behavior is discovered.
- Treat dated provider and client observations as revisable evidence. Revalidate them
  against current official documentation, live protocol behavior, and the installed
  Codex client before changing compatibility rules.
