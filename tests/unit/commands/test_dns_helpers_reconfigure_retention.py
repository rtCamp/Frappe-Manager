"""`_configure_dns_credentials` must not delete a retained stray while overwriting a label.

`DNSProviderConfig` is extra="allow": an unknown key inside a labelled `[ssl.dns_providers.<label>]`
entry is retained on load, per the retention ruling (fm never deletes a key it does not understand).
Reconfiguring that SAME label used to rebuild the entry from `DNSProviderConfig(provider=...,
email=..., api_token=..., api_key=...)`, four named kwargs with no way to see anything else the
loaded instance carried -- so a stray survived every read but was erased by the one write path meant
to update just the four real credential fields. Reconfiguring a label is the operator overwriting
THEIR OWN named fields for that label, not asking to clear an unrelated key in the same table, so the
fix mutates the loaded instance in place and the stray survives across both scopes.
"""

from unittest.mock import MagicMock, patch

from frappe_manager.commands.ssl import dns_helpers
from frappe_manager.ssl_manager import DNS_PROVIDER
from frappe_manager.ssl_manager.dns_provider import DNSProviderConfig

LABEL = "acct-a"
PROVIDER_NAME = "Cloudflare"


def _entry_with_stray(**overrides) -> DNSProviderConfig:
    base = {"provider": DNS_PROVIDER.cloudflare, "email": "old@example.com", "api_token": "old-token", "api_key": None}
    base.update(overrides)
    return DNSProviderConfig(**base, stray_key="operator_typo_value")


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.obj = {"services": MagicMock()}
    return ctx


def test_bench_scope_reconfigure_of_an_existing_label_preserves_its_stray(tmp_path):
    bench = MagicMock()
    bench.bench_config.dns_providers = {LABEL: _entry_with_stray()}
    bench.bench_config.root_path = tmp_path / "bench_config.toml"

    with (
        patch.object(dns_helpers.Bench, "get_object", return_value=bench),
        patch.object(dns_helpers, "get_output_handler", return_value=MagicMock()),
    ):
        dns_helpers._configure_dns_credentials(
            _ctx(), PROVIDER_NAME, "mybench", "new-token", None, "new@example.com", LABEL
        )

    updated = bench.bench_config.dns_providers[LABEL]
    assert updated.model_extra == {"stray_key": "operator_typo_value"}


def test_bench_scope_reconfigure_still_updates_the_real_credential_fields(tmp_path):
    """The regression risk of preserving too much: the operator's new values must actually land."""
    bench = MagicMock()
    bench.bench_config.dns_providers = {LABEL: _entry_with_stray()}
    bench.bench_config.root_path = tmp_path / "bench_config.toml"

    with (
        patch.object(dns_helpers.Bench, "get_object", return_value=bench),
        patch.object(dns_helpers, "get_output_handler", return_value=MagicMock()),
    ):
        dns_helpers._configure_dns_credentials(
            _ctx(), PROVIDER_NAME, "mybench", "new-token", None, "new@example.com", LABEL
        )

    updated = bench.bench_config.dns_providers[LABEL]
    assert updated.api_token == "new-token"
    assert updated.api_key is None
    assert updated.email == "new@example.com"


def test_bench_scope_configuring_a_new_label_still_creates_a_plain_entry(tmp_path):
    bench = MagicMock()
    bench.bench_config.dns_providers = {}
    bench.bench_config.root_path = tmp_path / "bench_config.toml"

    with (
        patch.object(dns_helpers.Bench, "get_object", return_value=bench),
        patch.object(dns_helpers, "get_output_handler", return_value=MagicMock()),
    ):
        dns_helpers._configure_dns_credentials(
            _ctx(), PROVIDER_NAME, "mybench", "tok", "key", "new@example.com", "acct-b"
        )

    created = bench.bench_config.dns_providers["acct-b"]
    assert created.model_extra == {}
    assert created.api_token == "tok"
    assert created.api_key == "key"


def test_global_scope_reconfigure_of_an_existing_label_preserves_its_stray():
    fm_config = MagicMock()
    fm_config.dns_providers = {LABEL: _entry_with_stray()}

    with (
        patch.object(dns_helpers.FMConfigManager, "import_from_toml", return_value=fm_config),
        patch.object(dns_helpers, "get_global_output_handler", return_value=MagicMock()),
    ):
        dns_helpers._configure_dns_credentials(
            _ctx(), PROVIDER_NAME, None, "new-token-g", None, "new-g@example.com", LABEL
        )

    updated = fm_config.dns_providers[LABEL]
    assert updated.model_extra == {"stray_key": "operator_typo_value"}


def test_global_scope_reconfigure_still_updates_the_real_credential_fields():
    fm_config = MagicMock()
    fm_config.dns_providers = {LABEL: _entry_with_stray()}

    with (
        patch.object(dns_helpers.FMConfigManager, "import_from_toml", return_value=fm_config),
        patch.object(dns_helpers, "get_global_output_handler", return_value=MagicMock()),
    ):
        dns_helpers._configure_dns_credentials(
            _ctx(), PROVIDER_NAME, None, "new-token-g", None, "new-g@example.com", LABEL
        )

    updated = fm_config.dns_providers[LABEL]
    assert updated.api_token == "new-token-g"
    assert updated.api_key is None
    assert updated.email == "new-g@example.com"
