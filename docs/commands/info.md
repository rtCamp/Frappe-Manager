## `fm info`

Show a bench's URL, credentials, apps, deploy history and live service state.

Every secret on the card is printed in cleartext: the administrator password, the site database password, and the basic auth password while a surface is protected. The shared global-db root credentials moved to fm services info.

**Usage**:

```console
$ fm info BENCH
```

**Arguments**:

* `BENCH`: Bench to act on. Omit to pick from the benches you have.


## Examples

### Show everything about a bench

```bash
fm info mybench
```

