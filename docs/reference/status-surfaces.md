# Status surfaces and single derivation

How fm reports a bench's state, and the rule that keeps those reports from disagreeing.

## Three tiers

fm reports state at three levels, which is the shape `kubectl` and `docker` settled on:

| tier | question | fm |
|---|---|---|
| list | what exists on this host | `fm list` |
| describe | the aggregated state of one bench, for a human | `fm info` |
| resource | one kind of thing, in detail | `fm ssl list`, `fm domain list`, `fm apps list` |

`fm ssl list` is not a competing status command. It is the resource tier, the equivalent of
`kubectl get certificates`, and it stays.

## The rule

> **List-shaped concerns keep their own command. State-shaped concerns are fields in `fm info`.**
>
> **`fm info` shows a reduction and a pointer, never a list.**

A concern is *list-shaped* when a bench can have N of it: certificates, domains, apps, sites. It is
*state-shaped* when a bench has exactly one answer: maintenance on or off, telemetry reporting or
not, auth on or off.

Applying the rule to what exists today:

| concern | shape | belongs |
|---|---|---|
| certificates | list | `fm ssl list` detail; one reduced line in `fm info` |
| domains | list | `fm domain list` detail; aliases line in `fm info` |
| apps | list | `fm apps list` detail; apps line in `fm info` |
| auth | state | `fm info` field (and `fm auth status` as the scriptable form) |
| admin tools | state | `fm info` field (and `fm tools status`) |
| maintenance | state | **missing from `fm info`** |
| telemetry | state | **missing from `fm info`** |

The two concerns missing from `fm info` are both state-shaped and both shaped as list-style
commands. That is the same mistake twice, not a coincidence.

Keeping the standalone commands matters: `fm info` is optimised for a human reading a card, and a
boolean a script can test must stay available on its own. `kubectl describe` deliberately has no
`-o json` for the same reason: machines read the resource, humans read the aggregate.

## Single derivation

A fact rendered by more than one command must be COMPUTED once. Two renderers over one derivation
can only differ in formatting; two derivations drift, and a drifted summary states something false
with full confidence.

`fm ssl list` already does this correctly: its card and its `--json` are both built from
`_bench_certificate_rows`, so they cannot disagree.

### Audit, at the time of writing

**Certificates: four independent derivations.** This is the one that has already produced wrong
output.

| path | used by | how it answers "does this bench have TLS" |
|---|---|---|
| `_bench_certificate_rows` (`commands/ssl/bench_helpers.py`) | `fm ssl list`, `fm ssl list --json` | every domain, with status, expiry, renewal, orphaned |
| `has_certificate()` + `get_primary_certificate()` + `get_certificate_expiry()` (`modules/bench_info.py`) | `fm info` | the PRIMARY site only |
| `_domain_has_certificate()` (`site_manager/bench_service.py`) | `fm list` | a local predicate over `ssl_certificates` |
| `has_certificate()` + `public_scheme()` | the URL scheme in several commands | boolean only |

Consequence, reproduced: a bench with certificates on two domains shows `2` in `fm ssl list` and
one expiry in `fm info`, and an alias certificate expiring is invisible in the summary. An
orphaned certificate is likewise invisible to everything except `fm ssl list`.

**Auth: one source, two renderers, duplicated per-site logic.** Both `fm info` and
`fm auth status` read `config.auth` and the per-site `sites[...].auth`, but each works out
separately which sites override the bench. The data is shared; the interpretation is not, so the
two can disagree about what a site's override means.

**Admin tools: shared predicate, different questions.** Both use
`bench_config.serves_admin_tools(site)` for routing, which is correct. "Configured" is asked two
ways, `compose_path.exists()` in `fm tools status` and container status in `fm info`, but those
are genuinely different questions (defined versus running) and should stay distinct.

**Maintenance: one derivation, not surfaced.** `commands/maintenance/_helpers.py` reads the fm
block out of the vhost config, and `_bench_domains` returns the per-domain map. Nothing else
computes it. It is simply absent from `fm info`, which is why a bench serving 503 to every visitor
is reported as `running`.

**Telemetry: one derivation, not surfaced.** `describe_newrelic(bench)` returns
`(enabled, reporting)`. Nothing else computes it; `fm info` does not call it.

**Domains and aliases: single source.** `bench_config.domains` is the only derivation, and
`fm info`, `fm domain list` and the ssl commands all read it. No action.

### What single derivation requires here

1. `fm info`, `fm list` and `fm ssl list` all reduce from `_bench_certificate_rows`. `fm info`
   takes the soonest expiry across every domain, the count, and the orphan count. `fm list` takes
   a boolean. Neither computes its own answer.
2. `fm info` calls `describe_newrelic` rather than re-reading config, and renders the line only
   when a provider is enabled, so an ordinary bench's card is unchanged.
3. `fm info` reads the maintenance state through the same helper `fm maintenance status` uses, and
   shows it in the header beside `running`, because `running` is false without it.
4. The per-site auth override list is computed once and shared by both renderers.

## Adding a status fact

Before adding a command or a field, answer in order:

1. **Is it list-shaped or state-shaped?** List gets a command; state gets an `fm info` field, and a
   standalone command only if a script needs to test it alone.
2. **Does anything else already compute it?** If yes, reuse that derivation. If it needs a
   different shape, reduce from the existing one rather than writing a second.
3. **Can its absence make `fm info`'s headline wrong?** If yes, it belongs in the header, not in a
   section.
4. **Does `fm info` need the whole list?** It never does. Show the reduction and name the command
   that enumerates it.
