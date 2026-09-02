## `fm ngrok`

Expose a running bench on a public ngrok URL.

Needs an ngrok auth token: pass --auth-token, set NGROK_AUTHTOKEN, or save one in fm's config.

**Usage**:

```console
$ fm ngrok BENCH [OPTIONS]
```

**Arguments**:

* `BENCH`: Bench to act on. Omit to pick from the benches you have.

**Options**:

* `-t, --auth-token`: ngrok auth token. Falls back to the one saved in fm's config.
* `--save-token/--no-save-token`: Save this token to fm's config for later runs, or leave the config alone. fm asks when a new token arrives and neither flag is passed.


## Examples

### Tunnel a running bench

```bash
fm ngrok mybench
```

### Supply a token and remember it

```bash
fm ngrok mybench --auth-token 2abcXYZ --save-token
```

## Related

- [Domains & Remote Access](../guides/domains.md)
