from frappe_manager.site_manager.domain_conflict import (
    check_domain_conflicts,
    validate_domains_unique,
)


def test_no_conflicts_empty_benches(tmp_path):
    conflicts = check_domain_conflicts(
        ["example.com"],
        benches_root=tmp_path,
    )
    assert len(conflicts) == 0


def test_validate_passes_when_no_conflicts(tmp_path):
    validate_domains_unique(
        ["example.com"],
        benches_root=tmp_path,
    )


def test_skip_check_allows_any_domain(tmp_path):
    validate_domains_unique(
        ["any.domain.com"],
        benches_root=tmp_path,
        skip_check=True,
    )


def test_case_insensitive_matching(tmp_path):
    domain_map = {}
    domain_map["site1.com"] = ("bench1", True)

    assert "site1.com" in domain_map
    assert "SITE1.COM".lower() in domain_map
    assert "Site1.COM".lower() in domain_map
