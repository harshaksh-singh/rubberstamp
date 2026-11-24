# Security policy

## Supported versions

No version has been tagged yet; the first release is planned as 0.1.0. Until then, security fixes go to the `main`
branch only. After the first release, fixes go to `main` and the latest release.

| Version | Supported |
|---|---|
| `main` | yes |
| any tagged release | none tagged yet |

## Reporting a vulnerability

Report it privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**
(<https://github.com/harshaksh-singh/rubberstamp/security/advisories/new>). Please do not open a public issue,
pull request or discussion about it. If the button is missing, open an issue that only asks for a private contact,
with no details.

Include what is affected (package, command or file, and the commit), how to reproduce it, and what an attacker gains.
This is a one-person research project: expect a first reply within a week, on a best-effort basis. Once a fix is on
`main`, the advisory is published with credit to the reporter, unless they ask otherwise.

## Threat model and scope

RubberStamp is a research environment that runs on your own machine. Its services are local by design.

- **rs-api** (`rs-api`, the helpdesk console's backend) has no authentication. It refuses to bind anything but the
  loopback interface (`127.0.0.1` or `localhost`), rejects requests whose `Host` header is not a loopback name, and
  allows cross-origin requests only from the console's origins (by default `http://localhost:3000` and
  `http://127.0.0.1:3000`).
- **The MCP server** (`rs-world serve`) speaks MCP over stdio by default. With `--transport http` it listens on
  `127.0.0.1` (FastMCP's default host) without authentication.
- **The helpdesk console** (`apps/helpdesk`) talks only to its own origin and to rs-api, under a Content Security
  Policy, and sends no analytics.
- **Agents are untrusted input.** A model driving the MCP tools or the API can only call the registered tools, and
  each episode writes to its own copy of a seeded SQLite world.

In scope: anything that lets another machine, or a web page in another origin, reach or drive rs-api or the MCP
server in their default configuration (for example a CORS, `Host`-header or DNS-rebinding bypass); a tool call that
reads or writes outside its episode's world; code execution from task, spec or result files in this repository; and
weaknesses in the GitHub Actions workflows.

Out of scope: running the services on a non-loopback interface or behind a proxy that exposes them (the code refuses
the first; the second removes the protection on purpose); the rented GPU machine that the training runbook
describes; and vulnerabilities in third-party dependencies (report those upstream, though a note here is welcome if
this project is affected in a way the upstream fix does not cover). Flaws in the planted graders are the research
subject, not vulnerabilities: report a grader that behaves differently from its documentation as an ordinary issue.

## Installing safely

The packages (`rs-core`, `rs-verify`, `rs-audit`, `rs-world`, `rs-suite`, `rs-train`, `rs-api`) are **not published
on PyPI**, and this project has not registered those names there. Install from a clone of this repository:

```bash
git clone https://github.com/harshaksh-singh/rubberstamp && cd rubberstamp
uv sync --locked
```

Inside the uv workspace, the packages' dependencies on each other resolve to the local source (`[tool.uv.sources]`),
and every other dependency is pinned with hashes in `uv.lock`. Do not install a member on its own with
`pip install git+...#subdirectory=packages/...` or from a built wheel: its dependencies on the other members are
plain names such as `rs-core`, which pip would look up on PyPI, where anyone can register them (dependency
confusion).

## How the repository is maintained

- GitHub Actions are pinned to full commit SHAs; checkouts do not keep credentials; each job gets the smallest
  permissions it needs; the Pages deploy runs only after CI passes on `main`.
- Dependabot proposes weekly updates for uv, npm and GitHub Actions, after a seven-day cooldown.
- CodeQL scans the Python, TypeScript and workflow code; zizmor lints the workflows; pip-audit and npm audit check
  the locked dependencies for known vulnerabilities. All of them run on pushes to `main`, on pull requests and
  weekly.
- `.env*` files and the `.private/` folder are git-ignored, and a pre-commit hook checks tracked files against a
  private denylist.
