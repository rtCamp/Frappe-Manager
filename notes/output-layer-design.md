# Output layer: what is wrong and what to build

Status: proposal. Nothing here is implemented.

The layer works, and every defect below is survivable today. What it lacks is
*enforcement*: almost every correctness property is a convention a call site has
to remember, and 31 `output.stop()` calls scattered through the codebase are the
receipts for the ones that did remember.

## Evidence

Verified against the code and, where marked, against a real TTY on the test
server. Line numbers are from the commit that introduced this note.

### 1. Two consoles, one Live

`RichOutputHandler` holds two rich `Console` objects (`console_singleton.py`) and
builds the spinner as:

```python
self.live = Live(self.spinner, console=self.stderr, transient=True)   # rich_output.py:54
```

The Live owns **one** of the two consoles. Rich coordinates writes made through
that console; it has no idea about `self.stdout`.

`Console.file` resolves the stream at write time and then *unwraps* rich's own
redirect proxy:

```python
file = self._file or (sys.stderr if self.stderr else sys.stdout)
file = getattr(file, "rich_proxied_file", file)     # console.py:757-763
```

so the redirect cannot rescue a second console either. `Console._live_stack`
(`console.py:750`) is pushed and popped and never consulted during a write.

Consequence: a write to `self.stdout` while the spinner is live is unprotected.
Exactly one method guards against it -- `print_data`, via `_pause_live`
(`rich_output.py:170`).

### 2. The stream a bare `print()` lands on depends on spinner state

`Live` defaults to `redirect_stdout=True, redirect_stderr=True`, and fm does not
override them. Measured on a real TTY (ssh -tt, both streams ttys):

```
before: stdout=TextIOWrapper stderr=TextIOWrapper
during: stdout=FileProxy     stderr=FileProxy      <- live spinner
after : stdout=TextIOWrapper stderr=TextIOWrapper
```

Both proxies feed the Live's console, which is the **stderr** console. So the 19
bare `print()` call sites outside `output_manager/` write to stdout normally and
to stderr while a spinner happens to be running. A stream contract that depends
on unrelated UI state is not a contract.

### 3. Four pause mechanisms, no rule for choosing

`_pause_live` (`rich_output.py:170`), `temporary_stop`
(`context_managers.py:35`, which reaches into the private `_current_text`),
`nested_spinner`, and the hand-rolled pause/resume inside `prompt_ask` and
`prompt_fuzzy`. `context_managers.py` carries no docstrings. The 31 bare
`stop()` calls are the fifth mechanism.

### 4. The logging decorator forwards by hand

`LoggingOutputHandler` lists every method it forwards and has no `__getattr__`
fallback. Two holes already exist:

- `emit_exit` is invisible through the wrapper; `main.py` unwraps it with
  `getattr(handler, "delegate")` to reach it.
- `is_spinner_active` is not forwarded, so wrapper state drifts from the
  delegate whenever `RichOutputHandler.display_error` stops the spinner itself.

Any method added to the base is silently not forwarded until someone notices.

### 5. Commands render instead of returning data

23 `typer.echo` sites and 19 bare `print()` sites bypass the handler. They exist
for a real reason, documented at `list.py:61`: rich cells truncate or fold long
values, and both corrupt a copied path. The cost is that **none of this output
exists in `--json` mode**.

Meanwhile `print_data` of a rich `Table` reaches JSON through
`json.dumps(default=str)` -- a repr -- while the *file log* renders it to plain
text properly (`logging_output.py:305`). The machine-readable consumer gets the
worst of the three renderings.

### 6. One live bug

`commands/compose.py:139` starts the spinner via `change_head`, then
`os.execvp`s docker compose at `:146` without stopping it. The process is
replaced with a transient Live still active, so the cursor is never restored.
Harmless when piped (no Live off a TTY); visible on a terminal.

### 7. Transient status is recorded as if it were a message

The spinner head fits neither `diagnostic` nor `data`, and the code already
treats it as its own kind everywhere it matters: its own JSON event types
(`start`, `change_head`, `update_head`, `stop`), DEBUG rather than INFO in the
file log (`logging_output.py:98-113`), and dedupe/suppression on a non-TTY that
no diagnostic has (`_print_noninteractive_head`).

Its semantics are last-write-wins, not append. `commands/update.py:479` calls
`change_head` inside a polling loop: one overwritten line on screen, but one
DEBUG log line and one JSON event PER POLL.

### 8. One job, two mechanisms (the pattern behind most of this note)

The clearest instance: acme.sh's output is relayed two different ways.

| site | how |
|---|---|
| `commands/ssl/acme_sh.py:79-82` | bare `print()`, permanent, unfiltered |
| `ssl_manager/acmesh_certificate_service.py:244` | `live_lines`, transient 4-line window, filtered |

This is NOT a deliberate split by intent. The `stream_with_exit_tracking`
generator and its `exit_code_holder` are copy-pasted verbatim between the two
files; only the consumer of the last line diverged. And the command version
starts a spinner first (`change_head("Running acme.sh")`, `:65`) and then
bare-prints into it, which is exactly the corruption case finding 2 describes.
It is duplication that drifted, and it must not be preserved as a feature.

The same defect class appears throughout:

| job | mechanism A | mechanism B |
|---|---|---|
| relay acme.sh output | bare `print()` (`acme_sh.py:82`) | `live_lines` (`acmesh_certificate_service.py:244`) |
| run a command in a container | `os.execvp` handover (`bench_docker.py:530`) | capture then replay with `print()` (`bench_docker.py:604-612`) |
| emit JSON for one command | `typer.echo(json.dumps(...))` under `--json` flag (`list.py:70`) | `print_data` event under global `--json` (`list.py:67`) |
| pause the spinner to write | `_pause_live` / `temporary_stop` | bare `stop()` / hand-rolled in `prompt_ask` |
| decide interactivity | `_is_interactive` (`rich_output.py:61`) | `is_interactive()` / `_tty_available` (`base.py:213`) |
| render a rich object for a non-terminal sink | `default=str` repr (`json_output.py`) | proper plain-text render (`logging_output.py:305`) |
| report a fatal error | `output.exit()` / `display_error` + `Exit(1)` | `typer.BadParameter` (exit 2, bypasses `--json`) / bare `print("[fm.error]...")` |
| decline a destructive action | typed-name ceremony, exit 1 (`delete.py:76`) | yes/no prompt, exit 0 (`delete.py:216`) |
| end a stream early | `stop_string` after filters (Rich) | before channel suppression (JSON) / defeated by decode failure (Silent) |
| end the program | base `exit()` (no spinner stop, no JSON seal) | Rich `exit()` (`builtins.exit`) / `main.py:_emit_json_exit` |
| track whether a spinner runs | `_spinner_active` on the delegate | a second copy on the logging wrapper |
| ask a question | `prompt_ask` (honours `default`, redacts passwords) | `prompt_fuzzy` (ignores `default` interactively, logs passwords verbatim) |
| say "nothing found" | `fm list` / `fm ssl list` | `(none)` / silence (`fm domain list`) |

Every row is one job with two answers, and in every row the two answers disagree
about something a user can observe. The point of this redesign is that each row
collapses to one.

### 8a. Where each of those collapses to

- **User-invoked passthrough commands hand the terminal over.** `fm compose`
  already does (`compose.py:146`). `fm ssl acme-sh` should too: it needs only
  an env var and the child's exit code, both of which `execvp` gives for free,
  with byte-exact fidelity and no relay code at all. That deletes the duplicated
  generator and the spinner-corruption.
- **Internal steps relay.** One renderer, one retention contract.
- Consequently `relay` needs NO transience parameter: user-invoked passthrough
  is `handoff()`, internal streaming is `relay()`. The earlier draft of this
  note argued for a parameter; that was preserving the accident.
- **`fm logs` is not a relay at all.** fm reads files itself
  (`site.py:1134-1144`); that output is fm's own result, so it is `data_raw`.

### 9. `live_lines` means two different things depending on the terminal

Interactive: a bounded `deque(maxlen=4)` inside the Live region, erased when the
stream ends. Non-interactive: every line printed permanently, stream split
preserved (`rich_output.py:304-356`). One call, two retention contracts, decided
by something the caller cannot see.

### 10. `line_filters` is ignored by the JSON handler

`json_output.py:246-286` buffers the whole child stream into one event and never
applies `line_filters`. Noise filtered off your screen is still in the machine
stream.

### 11. "Verbatim" relay is not verbatim

The pump (`utils/subprocess.py:34-72`) splits on `\r` as well as `\n`, strips
each line, and DROPS blank lines before any channel sees them. Byte-exact relay
is not currently possible, whatever the display layer does.

### 12. `--json` stdout is not valid JSONL (VERIFIED on the server)

```
$ fm --json domain list acme 2>/dev/null
{"event_type": "start", ...}
{"event_type": "stop", ...}
acme.localhost  primary        <- not JSON
acme            site           <- not JSON
{"event_type": "exit", ...}
```

Four list-like commands write their actual result with `typer.echo` straight to
stdout, which in `--json` mode is the JSONL stream:
`commands/apps/list.py:39-54`, `commands/domain/list.py:40-44`,
`commands/tools/status.py:44-46`, `commands/telemetry/status.py`. Any consumer
doing `json.loads` per line breaks. This is the concrete cost of finding 5.

### 13. `error(text, None)` returns in Rich and raises TypeError elsewhere (VERIFIED)

```
rich -> returned normally
json -> TypeError: exceptions must derive from BaseException
```

`rich_output.py:260-261` tolerates a falsy exception; `json_output.py:228` and
`silent_output.py:122` raise it unconditionally. Swapping the handler changes
whether the program continues.

### 14. Five mechanisms for a fatal error, with two different exit codes

`output.exit()` (6 uses), `display_error()` + `raise typer.Exit(1)` (173/131
uses), `raise FrappeManagerException`, `typer.BadParameter` (41 uses) and a bare
`output.print("[fm.error]Error:...")` + `Exit(1)` (`commands/self/stop.py:44`).
`typer.BadParameter` exits **2** and is rendered by click, so it never reaches
the `--json` stream at all; the others exit 1.

### 15. The same user action exits with two different codes

Declining a deletion: the typed-name ceremony exits **1** (`delete.py:76-84`),
the yes/no prompt exits **0** (`delete.py:216-225`). Both are "the user said
no" in the same command.

### 16. `live_lines` has three termination contracts

`stop_string` is defeated differently in each handler: by `line_filters` in Rich
(a filtered line never reaches the check, `rich_output.py:307-308,332-333`), by
channel suppression in JSON (`continue` precedes the check,
`json_output.py:271-278`), and by a decode failure in Silent
(`silent_output.py:152-163`). Three answers to "when does this stream end".

### 17. `--json` shows nothing until a stream finishes

`json_output.py:240-299` buffers every relayed line into ONE event emitted at
stream end. Rich streams live. A hung docker command produces continuous output
for a human and **zero bytes** for a machine.

### 18. Three `exit()` implementations

Base (inherited by JSON and Silent) does not stop the spinner and does not seal
the JSON stream -- sealing happens externally in `main.py:_emit_json_exit`.
Rich stops the spinner and uses `builtins.exit(1)` for `os_exit`, which is absent
without `site`. Logging wraps a `getattr` fallback that can never fire, because
`base.py:135` guarantees the attribute exists.

### 19. The logging wrapper's spinner state desynchronizes from the delegate

`is_spinner_active` is not forwarded, so when `RichOutputHandler.display_error`
or `exit` stops the spinner itself, the wrapper still reports it active. Two
copies of one piece of state, and they disagree after an ordinary error.

### 20. Password redaction covers one prompt and not the other

`logging_output.py:258-261` redacts passwords in `prompt_ask` responses;
`prompt_fuzzy` logs the response verbatim (`:283`) and also hard-codes
`[OUTPUT]` instead of using `log_prefix`.

### 21. A dead second prompt mechanism, kept alive by finding 3

`rich_output.py:463-486` is an `input()` fallback that can never run, because
`_is_interactive` and `is_interactive()` compute the identical value. The
`EMOJI_WARNING` constant (`:27`) exists only for that dead block. Duplicated
state kept duplicated code alive.

### 22. Dead and never-passed API

Zero non-test callers: `print_status` (a full abstract contract member across
all five handlers), `nested_spinner`, `spinner_or_pass`, `get_events`.
Accepted but never passed: `spinner(handle_keyboard_interrupt)`,
`exit(os_exit, error_msg)`, `JSONOutputHandler(persist_to_file)` (so its whole
persist branch is dead), `prompt_ask(**kwargs)` (silently dropped, while
`prompt_fuzzy` forwards them).

### 23. Stale references to systems that no longer exist

`globals.py:5` explains the singleton in terms of `DisplayManager`;
`main.py:25-26` and `site.py:1027` describe `richprint`. Both were deleted
(changelog: "Remove DisplayManager module completely", "Remove final richprint
usage"). `tests/README.md` points at the nonexistent
`.plans/output-migration-guide.md`.

### 24. Four styles of "nothing found"

`fm list`, `fm ssl list`, `fm apps list` ("(none)"), `fm tools status` and
`fm domain list` -- which prints **nothing at all** when there are no sites.

## Principles

From clig.dev and the CLI design consensus:

- stdout is the result the user asked for; stderr is logs, warnings, progress,
  prompts and diagnostics.
- with `--json`, stdout carries only parseable JSON, on success and on failure.
- commands produce typed data; rendering is a separate concern.

From rich's own documentation: do not stop a live display to print. Print
through it, and the output scrolls above. fm should make that automatic rather
than an obligation on every call site.

## Design

### One private writer

```
_emit(stream, renderable)      # take the lock, suspend the Live, write, resume
```

Nothing else touches a console. This turns "remember to pause" from a discipline
into an invariant that cannot be violated by construction. `_pause_live` and
`temporary_stop` collapse into it; the hand-rolled pauses in `prompt_ask` and
`prompt_fuzzy` become calls to it.

### Five channels

The four-channel sketch this note started with was audited against every output
call site. It does not survive: transient status is a channel of its own, and
relayed foreign output has two incompatible modes. Findings 7-9 below are the
evidence.

| # | channel | stream | retention |
|---|---|---|---|
| 1 | `diagnostic(level, text)` | stderr | permanent, markup on, emoji |
| 2 | `status(text)` | stderr | TRANSIENT, coalescing (last write wins) |
| 3 | `data(obj)` | stdout | permanent, rich-rendered |
| 4 | `data_raw(text)` | stdout | permanent, verbatim, no rich |
| 5 | `relay(lines, *, filters)` | the child's own | transient window (one contract) |

`data_raw` retires the `typer.echo` bypass class: those sites exist because rich
mangles copy targets, so give them a channel that writes exact bytes *and* is
routed, JSON-aware and spinner-safe.

`relay` retires the bare-`print()` class the same way.

Two things are NOT channels and must not be modelled as one:

- **prompts** are a bidirectional interaction. They already carry their own JSON
  event and a log mirror with password redaction
  (`logging_output.py:246-259`).
- **terminal handover** (`os.execvp`, inherited stdio) is the point where fm
  ceases to exist. It needs a `handoff()` primitive that flushes, stops the
  Live and logs the command -- not a line channel.

`--json` is a sink, not a channel.

No progress-bar channel is needed. fm has no percentage or x-of-y UI, and
docker's own progress bars are deliberately filtered out by
`DOCKER_LINE_NOISE` (`docker/__init__.py:104`).

### Logging becomes a sink, not a wrapper

One emitter, N sinks: terminal, file, JSON. `--json` adds a sink instead of
replacing the handler. The decorator hole disappears because there is nothing to
forward, and "visible in the log, missing from JSON" stops being expressible.

### A smaller core

Core: `emit(event)`, `prompt(...)`, `handoff()`, `is_interactive`. Every channel
builds an event and calls `emit`; `print` / `warning` / `info` / `debug` /
`display_error` become concrete helpers on the base over `diagnostic`. Handlers
implement four members instead of seventeen.

The event carries its channel, and the sink decides rendering from it: the
terminal sink coalesces `status` and windows a transient `relay`, the file sink
demotes `status` to DEBUG, the JSON sink emits one event per call and applies
the caller's filters (which it does not do today -- finding 10).

### `stop()` becomes private

Three mechanisms retire all 31 call sites:

| why the call exists | count | replaced by |
|---|---|---|
| write raw bytes | 8 | the writer pauses |
| print via fm's API | 7 | the writer pauses |
| stop before raising | 9 | one guaranteed teardown |
| hand the terminal over | 4 (+1 missing) | `with output.handoff():` |

`handoff()` releases the terminal and restores the cursor, and makes the
`compose.py` bug unwritable.

## Sequence

Seven independent, individually shippable steps. Step 0 first: it is a live
contract breakage, not a design concern.

0. make `--json` stdout parseable: migrate the four list-like commands off
   `typer.echo` (finding 12). Anything else can wait; this one is broken now.
1. the `_emit` chokepoint; delete the four duplicate pause mechanisms
2. `data` / `data_raw`; migrate the 23 `typer.echo` sites
3. `relay` + `handoff()`; collapse every row of the finding-8 table to one
   mechanism (`fm ssl acme-sh` becomes a handover, deleting the duplicated
   generator; `bench_docker`'s capture-and-replay becomes a handover; `fm logs`
   becomes `data_raw`; `fm list`'s two JSON paths become one); migrate the 19
   bare `print()` sites and the `live_lines` callers; fix findings 9, 10, 11, 16
4. `status` as its own channel; fix the head-update storm (finding 7)
5. one error path: pick ONE fatal-error mechanism and one exit code per outcome
   (findings 14, 15, 18); make `error()` agree across handlers (13)
6. sinks instead of the wrapper (fixes 17, 19); then shrink the ABC and delete
   the dead API (findings 21, 22) and the stale docstrings (23)

The `compose.py` cursor bug and the `prompt_fuzzy` password leak (finding 20)
are one-liners and should not wait for any of them.

## Acceptance

The property that makes this the LAST pass over this layer, rather than the
seventh unfinished one:

> No module outside `output_manager/` may construct a `Console`, call `print`,
> `typer.echo`, `click.echo`, or write to `sys.stdout`/`sys.stderr`.

That is mechanically checkable, so it belongs in a lint check or a test, not in
a review convention. Every defect in this note exists because the equivalent
rule was a convention.

## Rejected

- **Turning rich's `redirect_stdout` off or relying on it.** It buffers until a
  newline and reroutes through `console.print`, which reintroduces the exact
  mangling the copy-target sites exist to avoid. The redirect is also what makes
  finding 2 possible; the fix is to own the streams, not to lean on it harder.
- **Deleting `stop()` outright.** Terminal handoff is real.
- **A single big-bang rewrite.** Each step above stands alone and is separately
  verifiable.
- **A progress/percentage channel.** No such UI exists in fm, and docker's own
  progress bars are filtered out on purpose.
- **Modelling prompts or terminal handover as channels.** One is bidirectional,
  the other is the absence of output.
- **A transience parameter on `relay`.** An earlier draft of this note argued
  for one, reading the acme.sh split as two intents. It is copy-paste drift
  (finding 8): user-invoked passthrough is `handoff()`, internal streaming is
  `relay()`, and neither needs a flag. Do not preserve an accident by making it
  configurable.
- **Keeping any row of the finding-8 table as a choice.** Each job gets exactly
  one mechanism. A second way to do a job is how the first one rots.
