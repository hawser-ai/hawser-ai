# Hawser documentation

**Start here:** [quickstart](quickstart.md) — install, run the recorded example, run it live,
use your own documents.

## Concepts

| Page | What it covers |
|---|---|
| [Skills](concepts/skills.md) | Versioned bundles: inputs, schema, workflow, validators, prompts, lockfile |
| [Policy](concepts/policy.md) | Spending caps, allowed models, repair, review and check severities |
| [Validation](concepts/validation.md) | Core rules, the expression language, policy escalation, acceptance |
| [Review](concepts/review.md) | Review workspace, revisions, optimistic concurrency |
| [Evaluations](concepts/evals.md) | Datasets, scoring, reports, comparison, CI gates |
| [Models](concepts/models.md) | Providers, the registry, selection strategies, budgets, recording |
| [Tools](concepts/tools.md) | The broker, permissions, approvals, tool families |
| [Agent mode](concepts/agent.md) | Typed plans, the planner, the executor, context selection |
| [Images & OCR](concepts/images.md) | OCR engines, deskew, transform chains, layout, vision tools |
| [Server](concepts/server.md) | `hawserd`: API, durable execution, webhooks, SDK/CLI against a server |
| [Web app](concepts/web.md) | The screens served at `/app` |

## Running it

- [Operator guide](operator/) — configuration, deployment, backup and upgrade.
- [Security](../SECURITY.md) — reporting vulnerabilities, scope, hardening.

## Background

- [Architecture decisions](adr/) — why things are the way they are.
- [Run lifecycle](architecture/run-lifecycle.md) — the state machine and events.
- [Benchmark](benchmarks/invoice-po.md) — the reference skill across models and modes.
- [Roadmap](roadmap.md) — what shipped and what is next.
