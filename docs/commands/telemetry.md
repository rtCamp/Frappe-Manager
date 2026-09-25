# `fm telemetry`

Manage a bench's telemetry (APM) backends.

**Usage**:

```console
$ fm telemetry COMMAND [ARGS]...
```

| Command | Description |
|---|---|
| [`fm telemetry enable`](#fm-telemetry-enable) | Turn on APM reporting for a bench. |
| [`fm telemetry disable`](#fm-telemetry-disable) | Turn off APM reporting for a bench. |
| [`fm telemetry status`](#fm-telemetry-status) | Report which APM providers are configured on a bench and whether they are reporting. |

## `fm telemetry enable`

Turn on APM reporting for a bench.

The license key is recorded in bench_config.toml and passed to the agent through the container environment, so a key rotation is just this command again with the new key.

The agent's own config file (config/newrelic.ini) is seeded once and then yours: fm never rewrites it, so a disable/enable cycle keeps your tuning. Use --force-config to take fm's generated version back.

**Usage**:

```console
$ fm telemetry enable BENCH PROVIDER [OPTIONS]
```

**Arguments**:

* `BENCH`: Bench to act on. Omit to pick from the benches you have.
* `PROVIDER`: APM backend to enable.

**Options**:

* `--license-key TEXT`: Ingest license key for the provider. Required the first time; reused from bench_config.toml afterwards.
* `--force-config`: Overwrite the provider's on-disk agent config with fm's generated one, discarding local edits.  [default: false]

### Examples

#### Start reporting to NewRelic

Recreates the frappe container so the web process starts under the agent.

```bash
fm telemetry enable mybench newrelic --license-key YOUR_INGEST_KEY
```

#### Re-enable after a disable, reusing the stored key

Your edits to config/newrelic.ini are kept.

```bash
fm telemetry enable mybench newrelic
```

#### Rotate the ingest key

```bash
fm telemetry enable mybench newrelic --license-key NEW_KEY
```

#### Throw away local agent tuning and restore fm's generated newrelic.ini

```bash
fm telemetry enable mybench newrelic --force-config
```

## `fm telemetry disable`

Turn off APM reporting for a bench.

The web process is recreated without the agent, and the provider's license key is removed from the compose file. Nothing on disk is deleted: the recorded key stays in bench_config.toml and the agent's config file keeps your tuning, so re-enabling needs no arguments. Sweep an orphaned agent config with fm prune.

**Usage**:

```console
$ fm telemetry disable BENCH PROVIDER
```

**Arguments**:

* `BENCH`: Bench to act on. Omit to pick from the benches you have.
* `PROVIDER`: APM backend to disable.

### Examples

#### Stop reporting to NewRelic

The stored license key and your config/newrelic.ini are kept, so enabling again is one command.

```bash
fm telemetry disable mybench newrelic
```

## `fm telemetry status`

Report which APM providers are configured on a bench and whether they are reporting.

A provider needs both a stored license key and the enabled flag to report; either alone is shown as not reporting, because the web process falls back to plain Gunicorn.

**Usage**:

```console
$ fm telemetry status BENCH
```

**Arguments**:

* `BENCH`: Bench to act on. Omit to pick from the benches you have.

### Examples

#### Show APM state for a bench

```bash
fm telemetry status mybench
```
