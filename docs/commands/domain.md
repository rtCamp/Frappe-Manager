# `fm domain`

Manage a bench's alias domains.

**Usage**:

```console
$ fm domain COMMAND [ARGS]...
```

| Command | Description |
|---|---|
| [`fm domain add`](#fm-domain-add) | Add alias domains to a bench's site. |
| [`fm domain remove`](#fm-domain-remove) | Remove an alias domain from whichever site of the bench serves it. |
| [`fm domain list`](#fm-domain-list) | List every site's primary domain and its alias domains, one per line. |

## `fm domain add`

Add alias domains to a bench's site.

A bare BENCH attaches the aliases to its primary site; name a site with BENCH/SITE when the bench serves more than one. 'all' is refused here -- an alias belongs to one site.

The address is the SITE you are adding to, and the domains follow as arguments, because they do not exist yet to be named. Removal is the mirror: fm domain remove BENCH/DOMAIN takes the domain itself.

No certificate is issued for a new alias; run fm ssl add BENCH/DOMAIN afterwards.

**Usage**:

```console
$ fm domain add BENCH(/SITE) DOMAIN... [OPTIONS]
```

**Arguments**:

* `BENCH(/SITE)`: Bench, or BENCH/SITE to act on one of its sites. Without a site part, the bench's primary site is used.
* `DOMAIN...`: Alias domains to add to the site, e.g. www.example.com api.example.com.

**Options**:

* `--allow-domain-conflicts`: Skip the uniqueness check against every other bench's domains.  [default: false]

### Examples

#### Add an alias domain to a bench's primary site

No certificate is issued yet; run fm ssl add afterwards.

```bash
fm domain add mybench www.example.com
```

#### Add several aliases in one call

```bash
fm domain add mybench www.example.com api.example.com
```

#### Add an alias to one site of a multi-site bench

```bash
fm domain add mybench/shop.example.com www.shop.example.com
```

#### Add a domain another bench already serves, deliberately

```bash
fm domain add mybench shared.example.com --allow-domain-conflicts
```

## `fm domain remove`

Remove an alias domain from whichever site of the bench serves it.

Takes the DOMAIN you are removing, not the site it belongs to: fm looks up which site serves it. That is the mirror of fm domain add, which takes the SITE you are adding to, because the domain does not exist yet to be named. Same rule as fm ssl remove BENCH/DOMAIN.

**Usage**:

```console
$ fm domain remove BENCH(/DOMAIN)
```

**Arguments**:

* `BENCH(/DOMAIN)`: Bench, or BENCH/DOMAIN to reach one hostname it serves. Without a domain part, the bench's primary site is used.

### Examples

#### Remove an alias from a bench

```bash
fm domain remove mybench/www.example.com
```

## `fm domain list`

List every site's primary domain and its alias domains, one per line.

Output is plain lines rather than a table, so a hostname can be copied out of it intact.

**Usage**:

```console
$ fm domain list BENCH [OPTIONS]
```

**Arguments**:

* `BENCH`: Bench to act on. Omit to pick from the benches you have.

**Options**:

* `--json`: Emit this command's result as JSON on clean stdout.  [default: false]

### Examples

#### List a bench's domains

```bash
fm domain list mybench
```
