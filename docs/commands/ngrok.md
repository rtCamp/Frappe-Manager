## `fm ngrok`

Expose a running bench on a public ngrok URL.

The tunnel rewrites the Host header on every request, so it reaches exactly one of the bench's hostnames: fm ngrok BENCH/DOMAIN reaches that one, and a bare fm ngrok BENCH reaches the bench's primary site. A bench serving several sites needs the domain named, because one tunnel cannot answer for all of them.

Needs an ngrok auth token: pass --auth-token, set NGROK_AUTHTOKEN, or save one in fm's config.

**Usage**:

```console
$ fm ngrok BENCH(/DOMAIN) [OPTIONS]
```

**Arguments**:

* `BENCH(/DOMAIN)`: Bench, or BENCH/DOMAIN to reach one hostname it serves. Without a domain part, the bench's primary site is used.

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
