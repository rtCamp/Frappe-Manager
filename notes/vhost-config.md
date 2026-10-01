# Proxy vhost configuration

How fm writes per-domain nginx configuration into the shared proxy, why it uses marker blocks
today, and what should replace them.

## Two tiers, two ownership models

fm writes nginx configuration at two levels, and they are not the same problem.

**The bench's own nginx.** fm owns the image and the template (`Docker/nginx/template.conf`), so
the template can include a directory:

```nginx
include /etc/nginx/custom/*.conf;
include /etc/nginx/custom/{{ site_name }}/*.conf;
```

Every concern gets its own file. `fm auth` and the admin tools write
`custom/<site>/admin-tools.conf`. Adding a concern is writing a file; removing it is deleting one.

**The shared proxy.** This is nginx-proxy, a third-party image whose template fm does not control.
Its per-domain extension point is one file, and the name is fixed:

> Unlike in the proxy-wide case, which allows multiple config files with any name ending in
> `.conf`, the per-VIRTUAL_HOST file must be named exactly after the VIRTUAL_HOST.

The generated server block confirms there is exactly one include per domain:

```nginx
server_name devssl.localhost;
listen 80;  listen 443 ssl;
include /etc/nginx/vhost.d/devssl.localhost;
location / { proxy_pass http://devssl.localhost; }
```

Note both ports are one server block with one include, so every directive fm writes applies to
plain HTTP and to TLS alike.

Marker blocks exist only at this second tier. They are a consequence of not owning the template,
not a preference.

## What is in that file today

Four fm components write `vhost.d/<domain>`, in four different ways:

| writer | marker | edit strategy | newline contract |
|---|---|---|---|
| `commands/maintenance/_helpers.py` | `# fm:maintenance` | prepend, regex strip | strips the remainder |
| `ssl_manager/vhost_config_manager.py` | `# fm:https-redirect` | prepend, regex strip | must NOT strip: add then remove has to be byte-exact |
| `site_manager/modules/hsts_manager.py` | `# fm:hsts` | prepend, regex strip | compares bytes to skip a needless reload |
| `site_manager/modules/upload_limit_manager.py` | none | matches its own directive text, appends | n/a |

Two of those contracts contradict each other on the same file. `VhostConfigManager` also carries
`_LEGACY_RE`, an `re.escape()` of exact prose used as a content-addressed identifier for benches
deployed before the markers existed, where one character of drift breaks removal on every one of
them.

## The invariant fm states is already false

fm's rule is that unmarked content in `vhost.d/<domain>` belongs to somebody else and is never
touched. `UploadLimitManager` writes `client_max_body_size` with no marker, so fm cannot tell its
own writing from an operator's.

That is the root cause of a visible bug, not a separate one: `fm maintenance status` reported
`custom vhost config present (no fm maintenance block)` on a bench fm had built minutes earlier,
because the only thing it can check is "a file exists and has no maintenance marker", which is true
of every bench with TLS. The message was describing fm's own upload limit as the operator's work.
It cannot be made correct while an unmarked writer exists.

## Ordering is accidental

All three marked writers PREPEND, so the order of blocks in the file is the reverse of the order
the commands ran in. Nothing declares what the order should be.

Measured on a live bench, this currently does no harm, and the reason is worth recording because it
is not obvious. With maintenance enabled, an HTTP request returns 301 to HTTPS and the HTTPS
request returns 503, whichever order the blocks sit in. A `return 503` triggers `error_page`, which
performs an internal redirect, and that re-runs the server level `if` directives against the new
URI. The maintenance block exempts its own page, so on the second pass the redirect block is what
answers.

The behaviour is emergent, undocumented, and pinned by no test. It is luck that the two blocks
commute, not design.

## What should replace it

Markers and per-concern files are not alternatives. One marker bootstraps the directory:

```nginx
# fm:include BEGIN
include /etc/nginx/vhost.d/devssl.localhost.d/*.conf;
# fm:include END
client_max_body_size 50m;        # an operator's own directive, still preserved verbatim
```

and every concern becomes a file whose name declares its position:

```
devssl.localhost.d/10-upload-limit.conf
devssl.localhost.d/20-https-redirect.conf
devssl.localhost.d/30-hsts.conf
devssl.localhost.d/40-maintenance.conf
```

Enabling a concern writes a file. Disabling it unlinks one. Updating overwrites. Ordering is the
filename. Asking whether a concern is active is asking whether its file exists.

This removes four regex implementations, two conflicting newline contracts, the byte-exact inverse
requirement, and eventually `_LEGACY_RE`, which is needed once more for migration and can then be
deleted. It leaves exactly one marked block in fm, written once and never rewritten.

It also makes "unmarked content is foreign" TRUE, because fm would no longer write anything
unmarked into that file. The `fm maintenance status` message stops being wrong as a consequence of
the design rather than as a patch on top of it.

### Two things that are not reasons to keep markers

**"Markers survive a foreign rewrite better."** They do not. A tool that REPLACES the file destroys
every marked block as surely as it destroys an include line. A tool that APPENDS is compatible with
either design. There is no failure mode where the marked blocks survive and the include line does
not.

**"The stub means fm owns the whole file."** It does not. The include line lives in its own marked
block, so foreign content is preserved exactly as it is today.

## One manager, or one per concern?

Both, split along the right seam.

**One component owns the FILE.** The mechanism is identical for every concern: ensure the bootstrap
include exists, write or unlink a fragment, decide ordering, reload the proxy only when bytes
changed. That is one implementation, not four, and it is where today's contradictions live. Four
copies is how `VhostConfigManager` ended up forbidding the `.strip("\n")` that
`maintenance/disable.py` performs on the same file.

**Each concern still owns its CONTENT.** What belongs in a maintenance block is a maintenance
question; HSTS headers belong with the certificate code; the upload limit belongs with bench
config. Moving that text into one class would couple features that have nothing to do with each
other beyond their destination.

So the shape is a registry, not a god object:

```
set_fragment(domain, name, content)   # write and reload if changed
remove_fragment(domain, name)         # unlink and reload if it existed
fragments(domain)                     # what is active, for reporting
```

with the ordering prefixes declared in one table in that module, so "what runs before what" is
reviewable in a single place instead of being whatever the operator's command order produced.

The reporting benefit falls out: `fm info` and `fm maintenance status` ask the same component what
is active, rather than each re-deriving it by pattern matching a shared file. That is the single
derivation rule from `status-surfaces.md`, applied to configuration instead of state.

## Verified against a running proxy

All three preconditions were tested on a live nginx-proxy, not assumed.

**A wildcard include of a directory that does not exist is accepted.** With
`include /etc/nginx/vhost.d/devssl.localhost.d/*.conf;` in place and no such directory,
`nginx -t` reports the configuration valid. So the bootstrap line can be written before any
fragment exists, and the last fragment can be removed without leaving a broken config behind.

**Fragments are read, from the exact path, in filename order.** Dropping a file containing an
invalid directive makes `nginx -t` fail naming that file
(`unknown directive ... in /etc/nginx/vhost.d/devssl.localhost.d/99-bad.conf:1`), which is positive
proof the include resolves; removing it restores a valid config. nginx sorts glob matches, so the
numeric prefixes order the fragments.

**docker-gen ignores a `<domain>.d` directory beside the host files.** The generated
`conf.d/default.conf` still contains exactly one server block for the domain with the directory
present.

One trap found while testing, worth knowing before writing fragments: a server level `add_header`
in a fragment does NOT reach responses produced by a `location` that defines its own `add_header`,
because nginx replaces rather than merges the set. That is ordinary nginx inheritance, not an
artefact of the fragment layout, but it means "the header did not appear" is not evidence that a
fragment was not loaded.

## The missing marker is already losing operator data

Testing the migration precondition, that add-then-remove returns a file with foreign content to its
exact bytes, found that two of the three marked writers hold that contract and the unmarked one
destroys data.

Starting from a file an operator wrote:

```nginx
# operator's own
client_max_body_size   200m;
proxy_read_timeout 300;
```

| writer | add then remove returns the original bytes |
|---|---|
| `https-redirect` | yes |
| `hsts` | yes |
| `upload-limit` | **no** |

`UploadLimitManager` is content addressed on the directive name rather than on a marker, so it
treats the operator's `client_max_body_size 200m;` as its own: setting an upload limit silently
overwrites their value, and removing one DELETES a directive fm never wrote. The operator's
`proxy_read_timeout` survives; their upload limit does not.

This is the concrete harm the missing marker causes, not a hypothetical. It is also unfixable in
place without giving that writer a marker, which is the same change the fragment design makes
unnecessary by giving it a file of its own.

## Remaining before implementation

The mechanism is proven; what is left is the migration itself. Existing benches hold three marked
blocks and one unmarked directive in a shared file, and moving them into fragments has to preserve
foreign content exactly, which the two compliant writers already demonstrate is achievable.
