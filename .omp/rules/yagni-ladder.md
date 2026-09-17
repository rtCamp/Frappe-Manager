---
description: "Climb the ladder before building: does it need to exist, is it already here, stdlib, native platform, installed dependency"
condition: "(?i)(let me|i'll|i will|we should|going to|plan to) (create|build|add|write|introduce|implement) (an?|our own|a new|some) [a-z ]{0,24}(abstraction|wrapper|factory|framework|base class|helper class|helper module|layer|generic solution|utility class)|abstraction layer|in case we (need|want)|for future (use|proofing)|might need (it|this|that) later|to be safe, i'?ll also|while i'?m (here|at it), i'?ll also (add|build|create)"
scope: "thinking, text"
interruptMode: prose-only
probes:
  fire:
    - "Let me create an abstraction layer for the transcript sources"
    - "I'll build a wrapper class around the existing builder"
    - "We should introduce a generic solution here"
    - "While I'm here, I'll also add a cache"
  silent:
    # Reuse and removal are the behaviours this rule wants; punishing the words
    # for them would teach you to stop narrating your reasoning.
    - "I'll reuse the existing builder from the host"
    - "The abstraction here is fine: it has three implementations"
    - "I'll inline that one-line wrapper"
    - "let me check whether the rules loaded by running ttsr list"
---

Stop. You are about to build something before establishing that it needs to exist.

Climb the ladder and stop at the first rung that holds:

1. **Does this need to exist?** No → skip it. The best code is the code never written.
2. **Already in this codebase?** → reuse it. Grep before you write. A near-duplicate you did not find is worse than the duplication you can see.
3. **Does the standard library do it?** → use it.
4. **Is it a native platform feature?** → use it. `<input type="date">` beats a date-picker component.
5. **Does an installed dependency already do it?** → use it. Check `pyproject.toml` before adding to it.
6. **Is it one line?** → make it one line.
7. **Only then**: the minimum that actually works.

The ladder runs *after* you understand the problem, not instead of understanding it. Read the code the change touches and trace the real flow first. Lazy about the solution, never about reading.

## What this rule is not

It is not permission to cut corners. Trust-boundary validation, error handling that prevents data loss, security, and accessibility are never on the chopping block. The code ends up small because it is *necessary*, not because it is golfed.

## The specific traps this fires on

- "I'll add an abstraction layer" — for how many implementations? One is not a layer, it is indirection.
- "in case we need it later" — you do not know that, and the code costs maintenance now.
- "while I'm here, I'll also…" — that is a second change riding in on the first. It belongs in its own turn, or nowhere.
- "our own implementation of X" — X almost certainly exists in the stdlib, the platform, or a dependency already installed.
- A wrapper whose body only forwards arguments — inline it.

State which rung stopped you, then continue.
