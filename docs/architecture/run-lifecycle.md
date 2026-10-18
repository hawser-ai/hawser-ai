# Run lifecycle

The authoritative transition table is `hawser.runtime.state.ALLOWED_TRANSITIONS`; tests
enumerate it, and the Go control plane (Phase 5) mirrors it from an exported edge list.

```
queued ─► admitting ─► planning ─► running ─► validating ─► accepted
                                     ▲  │          │  │
                                     │  ▼          │  ├──► needs_review ─► accepted | rejected
                             awaiting_approval     │  │
                                                   ▼  └──► rejected
                                                repairing ─► running
```

| State | Class | Meaning |
|---|---|---|
| `queued` | active | Accepted by the API, not yet picked up |
| `admitting` | active | Inputs are being probed, hashed and limit-checked |
| `planning` | active | A plan is being resolved from the skill or generated (agent mode) |
| `running` | active | Steps are executing |
| `awaiting_approval` | paused | A side-effecting tool needs a human approval record |
| `validating` | active | Validators are running over the assembled result |
| `repairing` | active | A bounded repair strategy is being applied; returns to `running` |
| `needs_review` | resting | Machine work is finished; a reviewer must accept or reject |
| `accepted` | terminal | Validation passed (or a reviewer accepted) |
| `rejected` | terminal | Policy or a reviewer rejected the result |
| `failed` | terminal | An error ended the run; `RunResult.error` is set |
| `cancelled` | terminal | The caller cancelled |

Rules:

- Any active or paused state may move to `failed` or `cancelled`.
- `needs_review` may be cancelled but cannot fail: no machine work remains.
- `status` is always computed by the runtime from validation results and policy. No model output
  sets it (blueprint §14, §31).
- A run that ends `needs_review` has a machine revision; a reviewer decision creates a reviewed
  revision and moves the run to `accepted` or `rejected` (blueprint §17).

Error → status mapping lives in `hawser.runtime.state.status_for_error`. `BudgetExceeded`
routes to `needs_review` when `policy.review_on_failure` is set, because a partial result with a
human decision is more useful than a hard failure.
