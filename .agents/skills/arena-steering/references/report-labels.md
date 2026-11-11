# Report Decision Labels

NEVER use question strings ending in ? or generic words (Decision, Answer, Choice, Option, Approve, Reject, Recommended, Value) as a question or option label.

## Worked examples

- Implementation choice
  - Wrong: Decision: Answer
  - Right: The method we should use: Share one helper class between tabs.
- Clause amendment
  - Wrong: Should the rule forbid direct pushes?: Approve
  - Right: Direct-push boundary rule: Forbid direct pushes to protected branches.
- Assessment next action
  - Wrong: Next action: Recommended
  - Right: Next action for baseline skill drift: Seed missing arena-steering baseline.
- Multi-select checkbox
  - Wrong: Options: Option A, Option B
  - Right: Validation gates to run before push: Lint Markdown prose, Check baseline parity
- Numeric or threshold input
  - Wrong: Value: 3
  - Right: Maximum retry count before aborting: 3
- Free-text input
  - Wrong: Input: staging
  - Right: Target deployment environment name: staging

## Annotated Markdown example

```markdown
<!-- question {"id":"tab-state-method","label":"The method we should use"} -->
How should tab state be shared across views?
- (x) Share one helper class between tabs. <!-- option {"label":"Share one helper class between tabs."} -->
- ( ) Keep separate state per tab. <!-- option {"label":"Keep separate state per tab."} -->
```
