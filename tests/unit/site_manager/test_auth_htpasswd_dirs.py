"""``write_htpasswd`` must materialise the directory chain the file lives in.

The htpasswd sits deep under a bench: ``configs/nginx/conf/http_auth/<bench>.htpasswd``.
When auth is enabled on a bench that never had it, several of those levels do not exist
yet, and the caller has nothing else that creates them -- so writing the credentials has
to create the whole chain. Getting this wrong fails ``fm update --auth-*`` with an
``OSError`` from inside nginx-conf generation, i.e. after other conf files were already
rewritten. Re-running over an existing chain must stay silent (it is the common case:
every ``fm`` run re-asserts the credentials).
"""

from pathlib import Path

from passlib.apache import HtpasswdFile

from frappe_manager.site_manager.modules.auth import write_htpasswd

_RELATIVE = Path("configs") / "nginx" / "conf" / "http_auth" / "mybench.localhost.htpasswd"


def test_creates_every_missing_directory_level(tmp_path):
    path = tmp_path / _RELATIVE
    assert not path.parent.exists()
    assert not path.parent.parent.exists()  # more than one level missing

    assert write_htpasswd(path, "admin", "s3cret") is True

    assert path.parent.is_dir()
    assert HtpasswdFile(str(path)).check_password("admin", "s3cret")


def test_an_existing_directory_chain_is_not_an_error(tmp_path):
    path = tmp_path / _RELATIVE
    path.parent.mkdir(parents=True)

    assert write_htpasswd(path, "admin", "s3cret") is True
    assert HtpasswdFile(str(path)).check_password("admin", "s3cret")


def test_rotating_credentials_reuses_the_existing_chain(tmp_path):
    path = tmp_path / _RELATIVE
    write_htpasswd(path, "admin", "s3cret")

    assert write_htpasswd(path, "admin", "rotated") is True
    assert HtpasswdFile(str(path)).check_password("admin", "rotated")
