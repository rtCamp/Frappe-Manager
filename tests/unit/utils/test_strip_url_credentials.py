"""A credential in a clone URL must not survive the clone.

git records the URL it cloned FROM as the remote, so `https://TOKEN@github.com/org/repo` leaves
the token in `apps/<app>/.git/config` indefinitely -- readable by anything that can read the
bench, and copied into every image baked from that workspace.
"""

import pytest

from frappe_manager.utils.helpers import strip_url_credentials

pytestmark = pytest.mark.timeout(15)


def test_a_token_is_removed_from_an_https_url():
    assert strip_url_credentials("https://ghp_secret@github.com/org/repo.git") == "https://github.com/org/repo.git"


def test_a_user_password_pair_is_removed_too():
    """Not every credential is a bare token; `user:password@` is the other spelling."""
    assert strip_url_credentials("https://user:pw@git.example.com/a/b") == "https://git.example.com/a/b"


def test_a_url_with_no_credential_is_unchanged():
    """Returned byte-for-byte: this runs on every clone, including the public ones."""
    url = "https://github.com/org/repo.git"
    assert strip_url_credentials(url) == url


def test_scp_syntax_is_left_alone():
    """`git@github.com:org/repo` has no scheme and its `@` separates a USERNAME, not a secret.
    Treating it as a credential would rewrite a working SSH remote into a broken one."""
    url = "git@github.com:org/repo.git"
    assert strip_url_credentials(url) == url


def test_a_token_containing_an_at_sign_is_fully_removed():
    """Split on the LAST `@` in the authority, or a credential with an `@` in it leaks a fragment."""
    assert strip_url_credentials("https://us@er:p@ss@github.com/org/repo") == "https://github.com/org/repo"


def test_a_credential_inside_an_error_message_is_redacted():
    """GitPython quotes the whole git command line in its exceptions, so the clone URL rides into
    the log inside a message fm did not compose."""
    from frappe_manager.utils.helpers import redact_credentials_in_text

    said = redact_credentials_in_text("Cmd(git clone https://ghp_secret@github.com/a/b.git) failed")

    assert "ghp_secret" not in said
    assert "https://github.com/a/b.git" in said


def test_text_with_no_credential_is_unchanged():
    from frappe_manager.utils.helpers import redact_credentials_in_text

    text = "cloned https://github.com/a/b.git at ref v1"
    assert redact_credentials_in_text(text) == text
