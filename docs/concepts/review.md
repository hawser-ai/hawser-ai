# Review and revisions

Runs that end `needs_review` wait for a person (blueprint §17). The machine output is
immutable; a review produces a separate **reviewed revision**.

```bash
hawser review list                       # open reviews + needs_review runs not yet opened
hawser review show run_…                 # fields with issues, current values, evidence text, edits so far
hawser review edit run_… --set /invoice/vendor_name='"Northwind Components Ltd"' --comment "from the header"
hawser review approve run_… --comment "checked"   # -> accepted, reviewed revision created
hawser review reject run_…                         # -> rejected
hawser runs export run_… --revision machine|reviewed|latest
```

- `--set POINTER=VALUE` takes JSON (`123.4`, `"text"`, `null`) or a bare string.
- Edits are **decisions** on the review (field, old, new, reviewer, time, comment).
- Every change bumps the review `version`; pass `--version N` to fail on concurrent edits
  (`review_conflict`), the local equivalent of `If-Match`.
- Approval creates `Revision(kind="reviewed", parent=<machine revision>)`, moves the run to
  `accepted`, and emits `review.decided`. `hawser inspect` / `Client.get` show the latest view;
  `runs export --revision machine` always returns the untouched model output.

From Python: `runtime.reviews.open / edit / approve / reject / revisions / current_result`.

## After approval: what `get()` returns

The machine result is immutable. Once a reviewer approves, `get()` (and `GET /v1/runs/{id}/result`)
returns the machine result overlaid with the reviewed revision: `status` from the run,
`result` from the revision, `revision_id` pointing at it. The machine's error-severity
validation issues stay in the report for the record, downgraded to `info` with
`details.resolved_by = <revision id>`, because an accepted result cannot carry error-severity
issues. `hawser runs export --revision machine` still shows the untouched machine view.
