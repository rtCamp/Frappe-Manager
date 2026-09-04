"""ImageRef: the one place fm decomposes a ``[domain[:port]/]path[:tag][@digest]`` reference.

Table-driven over the shapes that matter: a bare name, a Docker Hub short name, a
domain-qualified reference, a multi-segment path, and a ``host:port`` domain -- each with
and without a tag -- plus digest forms both bare and combined with a tag. Every row is checked
against every question a caller asks (``has_tag``, ``is_digest_pinned``, ``is_pinned``,
``normalized_domain``, ``name``), so a change to the parser shows up as a diff in exactly the
cells it should touch, per fm's shape matrix contract (#digest-refs).
"""

import pytest

from frappe_manager.utils.helpers import ImageRef, has_explicit_tag, is_digest_pinned

# columns: image_ref, domain, path, tag, digest
SHAPES = [
    ("app", None, "app", None, None),
    ("app:v1", None, "app", "v1", None),
    ("org/app", None, "org/app", None, None),
    ("org/app:v1", None, "org/app", "v1", None),
    ("ghcr.io/org/app", "ghcr.io", "org/app", None, None),
    ("ghcr.io/org/app:v1", "ghcr.io", "org/app", "v1", None),
    ("ghcr.io/org/team/app", "ghcr.io", "org/team/app", None, None),
    ("ghcr.io/org/team/app:v1", "ghcr.io", "org/team/app", "v1", None),
    ("localhost:5000/app", "localhost:5000", "app", None, None),
    ("localhost:5000/app:v1", "localhost:5000", "app", "v1", None),
    ("app@sha256:abc", None, "app", None, "sha256:abc"),
    ("ghcr.io/org/app:v1@sha256:abc", "ghcr.io", "org/app", "v1", "sha256:abc"),
]


@pytest.mark.parametrize(("ref", "domain", "path", "tag", "digest"), SHAPES, ids=[s[0] for s in SHAPES])
def test_parse_decomposes_every_shape(ref, domain, path, tag, digest):
    assert ImageRef.parse(ref) == ImageRef(domain=domain, path=path, tag=tag, digest=digest)


@pytest.mark.parametrize(
    ("ref", "expected_host"),
    [
        ("app", "docker.io"),
        ("app:v1", "docker.io"),
        ("org/app", "docker.io"),
        ("org/app:v1", "docker.io"),
        ("ghcr.io/org/app", "ghcr.io"),
        ("ghcr.io/org/app:v1", "ghcr.io"),
        ("ghcr.io/org/team/app", "ghcr.io"),
        ("ghcr.io/org/team/app:v1", "ghcr.io"),
        ("localhost:5000/app", "localhost:5000"),
        ("localhost:5000/app:v1", "localhost:5000"),
        ("app@sha256:abc", "docker.io"),
        ("ghcr.io/org/app:v1@sha256:abc", "ghcr.io"),
    ],
    ids=[s[0] for s in SHAPES],
)
def test_normalized_domain_defaults_to_docker_hub(ref, expected_host):
    assert ImageRef.parse(ref).normalized_domain == expected_host


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("app", False),
        ("app:v1", True),
        ("org/app", False),
        ("org/app:v1", True),
        ("ghcr.io/org/app", False),
        ("ghcr.io/org/app:v1", True),
        ("ghcr.io/org/team/app", False),
        ("ghcr.io/org/team/app:v1", True),
        # A registry host:port is not a tag; the colon belongs to the host.
        ("localhost:5000/app", False),
        ("localhost:5000/app:v1", True),
        # A bare digest is not a tag, even though the raw text has a colon in it.
        ("app@sha256:abc", False),
        # This one really does carry both -- the digest does not erase the tag.
        ("ghcr.io/org/app:v1@sha256:abc", True),
    ],
    ids=[s[0] for s in SHAPES],
)
def test_has_explicit_tag(ref, expected):
    assert has_explicit_tag(ref) is expected


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("app", False),
        ("app:v1", False),
        ("localhost:5000/app", False),
        ("localhost:5000/app:v1", False),
        ("app@sha256:abc", True),
        ("ghcr.io/org/app:v1@sha256:abc", True),
    ],
)
def test_is_digest_pinned(ref, expected):
    assert is_digest_pinned(ref) is expected
    assert ImageRef.parse(ref).is_digest_pinned is expected


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("app", False),
        ("app:v1", True),
        ("app@sha256:abc", True),  # pinned by digest alone still counts as pinned
        ("ghcr.io/org/app:v1@sha256:abc", True),
        ("localhost:5000/app", False),
        ("localhost:5000/app:v1", True),
    ],
)
def test_is_pinned_accepts_either_tag_or_digest(ref, expected):
    assert ImageRef.parse(ref).is_pinned is expected


@pytest.mark.parametrize(
    ("ref", "expected_name"),
    [
        ("app:v1", "app"),
        ("org/app:v1", "org/app"),
        ("ghcr.io/org/app:v1", "ghcr.io/org/app"),
        ("ghcr.io/org/team/app:v1", "ghcr.io/org/team/app"),
        ("localhost:5000/app:v1", "localhost:5000/app"),
        ("app@sha256:abc", "app"),
        ("ghcr.io/org/app:v1@sha256:abc", "ghcr.io/org/app"),
    ],
)
def test_name_is_the_reference_minus_tag_and_digest(ref, expected_name):
    assert ImageRef.parse(ref).name == expected_name
