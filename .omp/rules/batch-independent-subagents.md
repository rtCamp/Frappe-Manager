---
name: batch-independent-subagents
description: "Spawn independent subagents in one batch instead of one-at-a-time serial dispatch"
condition: "\"tasks\"\\s*:\\s*\\[\\s*\\{(?:(?!\\},\\s*\\{)[\\s\\S])*?\\}\\s*\\]"
scope: "tool:task"
---

## One task per `task` call is almost always wrong

This batch has a single entry in `tasks[]`. Before sending it, ask: is there genuinely nothing else that could run right now?

Usually there is:

- **Read-only research alongside the write.** A `scout` mapping call sites, checking test coverage, or auditing docs does not conflict with an agent editing code.
- **Independent verification.** The gate run, the live check, the doc sweep — these do not need to wait for the implementation to finish.
- **Sibling slices.** Different files, different commands, different tiers of the same matrix.

Serial dispatch costs a full round trip per step. Ten one-task calls where three batches would do is a 3x efficiency loss, and it is invisible because each individual call looks reasonable.

## Decompose before dispatching

Map the work into slices, then send every genuinely independent slice in ONE `tasks[]` array:

```
task(tasks=[
  {name: "FixTheThing",   agent: "task",  task: "..."},
  {name: "AuditCallsites", agent: "scout", task: "..."},
  {name: "CheckDocsDrift", agent: "scout", task: "..."},
])
```

Up to 32 run concurrently. Read-only research MUST go on `scout`, which is faster.

## When one task genuinely is correct

Only when the next step **strictly depends** on this one's output, or when concurrent edits would collide on the same file. If so, say which dependency forces it, and pair it with any read-only work that can run alongside.

Two concurrent writers on one file is the only hard prohibition. Everything else parallelizes: state shared contracts up front in `context`, tell each agent to skip formatters, linters and project-wide test suites, and run those once at the end.