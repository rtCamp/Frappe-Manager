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

Takes the address grammar, not an argument: once a domain exists it is addressable, the same way fm ssl remove BENCH/DOMAIN is. Creation takes arguments because the domain does not exist yet; removal takes the address.

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

Copy targets get PLAIN lines, not table cells (commands/list.py:61): a rich cell would truncate or fold a long hostname, corrupting anything copied out of it.

**Usage**:

```console
$ fm domain list BENCH
```

**Arguments**:

* `BENCH`: Bench to act on. Omit to pick from the benches you have.

### Examples

#### List a bench's domains

```bash
fm domain list mybench
```
