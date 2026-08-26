"""The global DNS-01 credential set, and why the loader still reads the table it replaced.

`[cloudflare]` was the pre-0.20.0 home of the default account. It is gone from the model, and the
0.20.0 migration moves it into the `cloudflare` label. That migration is NOT sufficient on its own,
which is the point of this file: `export_to_toml` rebuilds the whole document from the model, so any
command that writes fm_config.toml drops a table the model cannot represent, and `migrate_services`
never runs once the infrastructure version is already current. Found on a real host, where one
ordinary write emptied a live Cloudflare key. The loader therefore folds the old table forward, and
the next write leaves only the new shape behind.
"""

from frappe_manager.metadata_manager import FMConfigManager

_VERSION = 'version = "0.20.0.dev0"\n'


def _config(tmp_path, body: str):
    path = tmp_path / "fm_config.toml"
    path.write_text(_VERSION + body)
    return path


def test_a_pre_0_20_credential_table_is_folded_into_the_default_label(tmp_path):
    path = _config(tmp_path, '[cloudflare]\nemail = "ops@example.com"\napi_key = "cf_LIVE"\n')

    entry = (FMConfigManager.import_from_toml(path).dns_providers or {})["cloudflare"]

    assert entry.api_key == "cf_LIVE"
    assert entry.email == "ops@example.com"
    assert entry.provider.value == "cloudflare"


def test_an_ordinary_write_does_not_destroy_a_pre_0_20_credential(tmp_path):
    """The regression itself. Before the fold, this write lost the key outright."""
    path = _config(tmp_path, '[cloudflare]\napi_key = "cf_LIVE"\n')

    FMConfigManager.import_from_toml(path).export_to_toml(path)

    text = path.read_text()
    assert "cf_LIVE" in text
    assert "[ssl.dns_providers.cloudflare]" in text
    assert "[cloudflare]" not in text


def test_a_configured_label_wins_over_the_table_it_replaced(tmp_path):
    """Both present means the file is mid-conversion; the new spelling is the authority."""
    path = _config(
        tmp_path,
        '[cloudflare]\napi_key = "cf_OLD"\n[ssl.dns_providers.cloudflare]\napi_token = "cf_NEW"\n',
    )

    entry = (FMConfigManager.import_from_toml(path).dns_providers or {})["cloudflare"]

    assert entry.api_token == "cf_NEW"
    assert entry.api_key is None


def test_an_empty_credential_table_creates_no_label(tmp_path):
    """A table holding no secret is not a credential, and inventing an entry for it would put an
    unusable set in front of the resolver."""
    path = _config(tmp_path, '[cloudflare]\nemail = "ops@example.com"\n')

    assert FMConfigManager.import_from_toml(path).dns_providers is None


def test_a_file_with_no_credentials_gains_no_ssl_table(tmp_path):
    path = _config(tmp_path, "")

    config = FMConfigManager.import_from_toml(path)
    config.export_to_toml(path)

    assert config.dns_providers is None
    assert "[ssl" not in path.read_text()
