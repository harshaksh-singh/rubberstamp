# Industry checklist

A review of the repository against common practice for open-source research code, RL environments and benchmarks,
taken on 2026-11-24 and updated after the CI, packaging and community-file fixes on the same day. Fixes in other areas
(the site, rs-api, the console, rs-train and the graders) were still being made when this was written; their rows
say "in progress" until they land.

Status: **pass** meets the item; **partial** meets part of it; **fail** does not; **in progress** is being fixed in
another change. "Was" gives the status at the review, where it changed.

## Packaging

| Item | Status | Notes |
|---|---|---|
| pyproject metadata (description, readme, authors, URLs, requires-python) | pass (was partial) | Every package and the root have a description, readme, the same `authors`, `[project.urls]` (Homepage, Source, Documentation, Changelog, Issues) and `requires-python = ">=3.12, <3.14"`. `scripts/check_metadata.py` checks that they agree. |
| Trove classifiers | pass (was fail) | Development status, audience, Python versions, topic and `Typing :: Typed`. 3.13 is listed only for rs-core, rs-verify and rs-world, which CI tests on 3.13 and whose dependencies all allow it (openadapt-flow, under rs-audit and rs-suite, declares `Requires-Python <3.13`). No license classifier: PEP 639 replaces it with the license expression. |
| License in package metadata | pass (was fail) | `license = "MIT"` and `license-files = ["LICENSE"]` in every package, with a copy of the root LICENSE in each package directory (PEP 639 paths cannot leave it; the metadata check requires the copies to be identical). The built rs-verify wheel has `License-Expression: MIT` and `dist-info/licenses/LICENSE`. |
| py.typed marker | pass | Present in all 7 packages and shipped in the wheels. |
| Version single-sourced | pass (was fail) | One version, 0.1.0, in the root, all 7 packages, `CITATION.cff` and `apps/helpdesk/package.json` (the root was 0.0.1). uv_build needs a static version per package, so a check stands in for a single file: `scripts/check_metadata.py` in CI and pre-commit. No package exposes `__version__`; `importlib.metadata.version("rs-world")` works. |
| Build backend pinned and working | pass | `uv_build>=0.13.7,<0.14`; `uv build --all-packages` builds all 7. |

## Reproducibility

| Item | Status | Notes |
|---|---|---|
| Locked dependencies | pass | `uv.lock` (with hashes) and the two `package-lock.json` files; CI installs with `uv sync --locked` and `npm ci`. |
| Exact pins for data-generating dependencies | partial | Unchanged here. `openadapt-flow==3.4.0` and `trl==1.18.2` are exact; `faker>=40.63.0`, which generates the world's names, is a lower bound in rs-world (locked to one version in `uv.lock`). Outside this change. |
| Seeds and pinned world digest | pass | World seeds, a design seed and per-seed instances; the seed-42 digest is pinned by a test. |
| Deterministic, byte-for-byte results | pass | Tests regenerate the committed results and compare bytes; `npm run charts` reproduces `docs/assets/*.svg`, and CI now checks that. |
| Run cards / provenance | partial | Unchanged. Run cards record families, tool schemas and the openadapt-flow version, but no commit or package versions; adding them would change every committed result. |
| Documented hardware | pass | The rs-train runbook names the GPU and drivers; everything else runs on a laptop. |

## Testing

| Item | Status | Notes |
|---|---|---|
| Unit, integration and property tests | pass | 2,180 tests collected at the review, including Hypothesis property tests, API/MCP parity tests and byte-pinned result regeneration. |
| Coverage reporting | pass (was fail) | `pytest-cov`; CI's python job runs the suite with `--cov` and writes the coverage table to the job summary. No threshold is enforced and nothing is uploaded to an external service. |
| Type checking | pass (was partial) | `[tool.pyright]` in the root `pyproject.toml` (standard mode, Python 3.12) covers `packages/*/src`, `scripts/` and `tests/`, all at 0 errors; CI runs `pyright -p .`, so a `pyrightconfig.json` in a parent directory cannot take over. `packages/*/tests` are excluded, with the reason in the config (about 110 errors, nearly all Optional access in assertions). |
| Lint and format | pass | ruff check and format, plus eslint, `astro check` and `tsc` in CI. |

## CI/CD

| Item | Status | Notes |
|---|---|---|
| CI least-privilege permissions | pass (was partial) | ci.yml: `contents: read`. site.yml: no workflow permissions; build job `contents: read`; deploy job `pages: write` and `id-token: write` only. codeql.yml and security.yml grant per job. |
| Actions pinned (SHA) | pass (was partial) | Every action is pinned to a full commit SHA with a version comment, resolved from the tags; every checkout sets `persist-credentials: false`. zizmor reports no findings. Dependabot's github-actions updates move the SHAs, after a 7-day cooldown. |
| Concurrency and timeouts | pass (was partial) | Every job has `timeout-minutes`. Runs for branches and PRs cancel when superseded; runs on `main` do not. |
| Caching | partial | uv and npm caches are on. The rs-api job now installs rs-api's dependencies only (no torch; its 334 tests pass in that environment). The python job still downloads about 3 GB of CUDA wheels per run, because rs-train needs torch; the README says so. |
| Test matrix | partial (was partial) | Ubuntu only. Python 3.12 for the whole workspace; a 3.13 job runs the tests of the packages that declare 3.13, rs-core, rs-verify and rs-world with its MCP extra (557 passed locally on 3.13.18). rs-suite's audit extra, rs-train and rs-api are not tested on 3.13: rs-audit's dependency openadapt-flow 3.4.0 declares `Requires-Python <3.13`. |
| CI green and deploy gated on CI | pass (was partial) | site.yml runs on `workflow_run` of CI and builds only after a successful CI run of a push to (or a manual run on) this repository's `main`, at that commit. The new workflows pass actionlint and zizmor locally; their first runs on GitHub are pending. |

## Supply chain and security

| Item | Status | Notes |
|---|---|---|
| SECURITY.md / disclosure policy | pass (was fail) | Private reporting through GitHub security advisories, supported versions, the local-only threat model of rs-api and the MCP server, and how to install safely. GitHub's private vulnerability reporting has to be switched on in the repository settings. |
| Dependabot | pass | uv, npm (site and console) and github-actions, grouped weekly, with documented holds and a 7-day cooldown. |
| SAST / vulnerability audit | pass (was fail) | CodeQL (actions, javascript-typescript, python), zizmor, pip-audit over the locked graph and `npm audit --omit=dev --audit-level=high` for both apps, on pushes to `main`, on pull requests and weekly. Two setuptools advisories are ignored with a written reason (vLLM 0.33 in the gpu extra caps setuptools below the fixed versions, and nothing here builds or installs with setuptools). The site's two moderate npm advisories (fflate through satori, build time only) are below the threshold. |
| Secret-scanning hygiene | pass | `.env*` and `.private/` are git-ignored; the clean-room hook checks tracked files. |
| Dependency-confusion resistance | partial (was fail) | Documented, not removed: SECURITY.md, CONTRIBUTING.md and the README say the packages are not on PyPI and must be installed from a clone with `uv sync`, never one by one with pip. The names (rs-core and the others) are still unregistered and wheels still declare `Requires-Dist: rs-core`; renaming the distributions or registering the names is a decision for the maintainer. |

## Community

| Item | Status | Notes |
|---|---|---|
| LICENSE | pass | MIT at the root and in each package. |
| CONTRIBUTING, CODE_OF_CONDUCT, issue/PR templates | pass (was fail) | CONTRIBUTING.md (setup, checks, pinned results, adding a task, commits, releases, the clean-room rule), CODE_OF_CONDUCT.md (Contributor Covenant 2.1 by reference), issue forms for bugs and for feature or task proposals, and a pull request template. |
| CHANGELOG, release tags, semver | partial (was fail) | CHANGELOG.md in Keep a Changelog format, reconstructed from the design log and the git history, with everything under `[Unreleased]`. No tag yet: tagging v0.1.0 is the maintainer's decision. |
| CITATION.cff | pass (was partial) | Valid (cffconvert). Version 0.1.0 is marked as the planned first release, and there is no `date-released` until it is tagged. |

## Documentation

| Item | Status | Notes |
|---|---|---|
| README quickstart works verbatim | pass | Clone, `uv sync`, the audit and shortcut-matrix scripts reproduce the committed results. |
| Architecture documentation | pass | Package table, per-package READMEs, walkthroughs and the design log. |
| Environment / dataset card | partial | Unchanged here. The rs-world README documents schema, tools, enforcement, seeding and tasks, but has no intended-use, contamination or limitations section. |
| API reference | in progress | rs-api track. |
| Limitations / known issues | in progress | Site and README track. |
| Docs consistent with code | in progress | rs-train track (the 3-seed design, measured opens and "no git remote" statements). |

## Benchmark hygiene

| Item | Status | Notes |
|---|---|---|
| Canary string / contamination statement | in progress | Another track; lead to confirm the owner. |
| Held-out split | in progress | rs-train track (task.yaml's test split versus rs-train's held-out templates). |
| Versioned task set | partial | One version for the code and the task set, but no tag to cite yet. The seed-42 digest and per-task `version` fields are pinned. |
| Reward specification | pass | Binary reward (all checks pass), documented in the rs-verify README. |
| Failure taxonomy | pass | 10 leniency flags, 10 verifier-independent detectors and exploit labels. |
| Audit evaluated out-of-sample | in progress | Grader and results tracks (report the t21-t25 signal). |

## Containerization

| Item | Status | Notes |
|---|---|---|
| Dockerfile / devcontainer | fail | Unchanged here; the adapter work in flight may add a container. |
