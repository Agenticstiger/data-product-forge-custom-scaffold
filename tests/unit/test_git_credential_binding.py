"""A credential goes only where the operator allows, under a name the operator allows.

A contract may come from a pull request. It can say which environment variable it wants and
which url it wants to fetch, so it must not decide where a credential goes: the operator's
settings (the process environment) do. These tests pin that, and the rules around it.
"""

from __future__ import annotations

import hashlib
from typing import Any, List

import pytest

from data_product_forge_custom_scaffold.resolvers import git as git_resolver
from data_product_forge_custom_scaffold.resolvers.base import ResolutionError
from data_product_forge_custom_scaffold.resolvers.git import (
    GitResolver,
    _add_git_config,
    _bind_auth,
    _Credential,
)
from tests.helpers import git_source, patch_run

URL = "https://github.com/org/bundle"
SECRET = "ghp_VERYSECRETVALUE"
HOSTS = "FLUID_CUSTOM_SCAFFOLD_AUTH_HOSTS"
PREFIXES = "FLUID_CUSTOM_SCAFFOLD_SECRET_PREFIXES"


@pytest.fixture
def operator(monkeypatch):
    """An operator who allows github.com and holds a token for it."""
    monkeypatch.setenv(HOSTS, "github.com")
    monkeypatch.delenv(PREFIXES, raising=False)
    monkeypatch.setenv("FLUID_SCAFFOLD_TOKEN", SECRET)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "AKIAnotforyou")


def _no_git(monkeypatch) -> List[Any]:
    """Fail the test if anything starts a process: a refused source must not reach git."""

    def refuse(args: Any, kwargs: Any) -> Any:
        raise AssertionError(f"a process was started: {args}")

    return patch_run(monkeypatch, git_resolver, refuse)


def _source(url: str = URL, auth: Any = None, ref: str = "v1") -> dict:
    return git_source(url, ref, auth)


# ── hosts ────────────────────────────────────────────────────────────────────


def test_nothing_is_sent_anywhere_until_the_operator_lists_a_host(monkeypatch) -> None:
    monkeypatch.delenv(HOSTS, raising=False)
    monkeypatch.setenv("FLUID_SCAFFOLD_TOKEN", SECRET)
    _no_git(monkeypatch)

    with pytest.raises(ResolutionError) as refused:
        GitResolver().resolve(_source(auth={"secret_ref": "FLUID_SCAFFOLD_TOKEN"}))

    assert HOSTS in str(refused.value) and "github.com" in str(refused.value)
    assert SECRET not in str(refused.value)


def test_a_host_the_operator_did_not_list_gets_nothing(operator, monkeypatch) -> None:
    _no_git(monkeypatch)

    with pytest.raises(ResolutionError, match="attacker.example"):
        GitResolver().resolve(
            _source("https://attacker.example/x.git", {"secret_ref": "FLUID_SCAFFOLD_TOKEN"})
        )


def test_the_contract_cannot_widen_what_the_operator_allows(operator, monkeypatch) -> None:
    # Extra keys in `auth` (the schema allows them) change nothing.
    _no_git(monkeypatch)
    auth = {"secret_ref": "FLUID_SCAFFOLD_TOKEN", "hosts": ["attacker.example"], "allow": "*"}

    with pytest.raises(ResolutionError, match="attacker.example"):
        GitResolver().resolve(_source("https://attacker.example/x.git", auth))


@pytest.mark.parametrize(
    ("listed", "url", "allowed"),
    [
        ("github.com", "https://github.com/o/r", True),
        ("GitHub.COM", "https://github.com/o/r", True),
        ("github.com", "https://GITHUB.com/o/r", True),
        ("a.example, github.com ,b.example", "https://github.com/o/r", True),
        ("github.com", "https://api.github.com/o/r", False),  # exact, not a suffix
        ("github.com", "https://notgithub.com/o/r", False),
        ("", "https://github.com/o/r", False),
    ],
)
def test_hosts_match_exactly_and_without_regard_to_case(
    monkeypatch, listed: str, url: str, allowed: bool
) -> None:
    monkeypatch.setenv(HOSTS, listed)
    auth = {"secret_ref": "FLUID_SCAFFOLD_TOKEN"}

    if allowed:
        assert _bind_auth(url, auth) is not None
    else:
        with pytest.raises(ResolutionError):
            _bind_auth(url, auth)


# ── names ────────────────────────────────────────────────────────────────────


def test_a_contract_cannot_name_any_variable_it_likes(operator, monkeypatch) -> None:
    # The exfiltration path: any variable and any host. The host is even allowed here.
    monkeypatch.setenv(HOSTS, "attacker.example")
    _no_git(monkeypatch)

    with pytest.raises(ResolutionError) as refused:
        GitResolver().resolve(
            _source("https://attacker.example/x.git", {"secret_ref": "AWS_SECRET_ACCESS_KEY"})
        )

    assert "FLUID_SCAFFOLD_" in str(refused.value) and PREFIXES in str(refused.value)
    assert "AKIAnotforyou" not in str(refused.value)


@pytest.mark.parametrize(
    ("name", "allowed"),
    [
        ("FLUID_SCAFFOLD_TOKEN", True),
        ("FLUID_SCAFFOLD_ANYTHING_ELSE", True),
        ("FLUID_SCAFFOLDING", False),  # the prefix ends in an underscore
        ("fluid_scaffold_token", False),  # names are case-sensitive
        ("GITHUB_TOKEN", False),
        ("PATH", False),
    ],
)
def test_the_default_prefix_is_fixed(operator, name: str, allowed: bool) -> None:
    if allowed:
        assert _bind_auth(URL, {"secret_ref": name}).name == name  # type: ignore[union-attr]
    else:
        with pytest.raises(ResolutionError, match="FLUID_SCAFFOLD_"):
            _bind_auth(URL, {"secret_ref": name})


def test_the_operator_can_allow_more_prefixes(operator, monkeypatch) -> None:
    monkeypatch.setenv(PREFIXES, "MYORG_TOKENS_, ci_")

    assert _bind_auth(URL, {"secret_ref": "MYORG_TOKENS_GITHUB"}) is not None
    assert _bind_auth(URL, {"secret_ref": "ci_bot"}) is not None
    assert _bind_auth(URL, {"secret_ref": "FLUID_SCAFFOLD_TOKEN"}) is not None  # still allowed
    with pytest.raises(ResolutionError):
        _bind_auth(URL, {"secret_ref": "GITHUB_TOKEN"})


@pytest.mark.parametrize("name", ["not a name", "1ABC", "A-B", "", None, 7, ["X"]])
def test_a_secret_name_must_be_a_plain_variable_name(operator, name) -> None:
    auth = {"secret_ref": name}
    if name in ("", None):
        assert _bind_auth(URL, auth) is None  # no auth asked for
    else:
        with pytest.raises(ResolutionError, match="env-var-shaped"):
            _bind_auth(URL, auth)


def test_a_refused_name_is_never_looked_up(operator, monkeypatch) -> None:
    looked_up: List[str] = []
    real_get = git_resolver.os.environ.get

    class Spy(dict):
        def get(self, key, default=None):  # type: ignore[override]
            looked_up.append(key)
            return real_get(key, default)

    monkeypatch.setattr(git_resolver.os, "environ", Spy(git_resolver.os.environ))
    _no_git(monkeypatch)

    with pytest.raises(ResolutionError):
        GitResolver().resolve(_source(auth={"secret_ref": "AWS_SECRET_ACCESS_KEY"}))

    assert "AWS_SECRET_ACCESS_KEY" not in looked_up


# ── schemes ──────────────────────────────────────────────────────────────────


def test_git_plus_https_with_auth_is_an_error_not_a_silent_anonymous_fetch(
    operator, monkeypatch
) -> None:
    _no_git(monkeypatch)

    with pytest.raises(ResolutionError, match=r"git\+https://.*use an https://"):
        GitResolver().resolve(
            _source("git+https://github.com/org/bundle", {"secret_ref": "FLUID_SCAFFOLD_TOKEN"})
        )


@pytest.mark.parametrize("url", ["ssh://git@github.com/org/bundle", "git@github.com:org/bundle"])
def test_auth_is_only_sent_over_https(operator, monkeypatch, url: str) -> None:
    _no_git(monkeypatch)

    with pytest.raises(ResolutionError, match="only sent over https"):
        GitResolver().resolve(_source(url, {"secret_ref": "FLUID_SCAFFOLD_TOKEN"}))


# ── credentials in the url ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://user:hunter2@github.com/org/bundle",
        "https://hunter2@github.com/org/bundle",
        "https://x-access-token:ghp_VERYSECRETVALUE@github.com/org/bundle",
        "ssh://user:hunter2@github.com/org/bundle",
    ],
)
def test_a_url_that_carries_credentials_is_refused_and_not_echoed(
    operator, monkeypatch, url: str
) -> None:
    # They would be written into the lock as mirror_url, which is committed.
    _no_git(monkeypatch)

    with pytest.raises(ResolutionError, match="must not carry credentials") as refused:
        GitResolver().resolve(_source(url))

    assert "hunter2" not in str(refused.value) and "VERYSECRET" not in str(refused.value)


def test_the_lock_records_the_url_as_written_and_nothing_secret(
    operator, monkeypatch, tmp_path
) -> None:
    # A user name alone on an ssh url is an account, not a credential: it stays.
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_CACHE", str(tmp_path))
    cache_dir = GitResolver._cache_dir_for("ssh://git@github.com/org/bundle", "v1")
    cache_dir.mkdir(parents=True)
    (cache_dir / "fluid-scaffold.yaml").write_text("apiVersion: forge.scaffold/v1\n")

    bundle = GitResolver().resolve(_source("ssh://git@github.com/org/bundle"))

    assert bundle.mirror_url == "ssh://git@github.com/org/bundle"


# ── the cache key ────────────────────────────────────────────────────────────


def test_a_source_without_auth_keeps_the_key_it_always_had() -> None:
    expected = hashlib.sha256(URL.encode()).hexdigest()[:16]

    assert GitResolver._cache_dir_for(URL, "v1").parent.name == expected


def test_the_key_names_the_credential_and_never_its_value(operator) -> None:
    plain = GitResolver._cache_dir_for(URL, "v1")
    with_a = GitResolver._cache_dir_for(
        URL, "v1", _bind_auth(URL, {"secret_ref": "FLUID_SCAFFOLD_A"})
    )
    with_b = GitResolver._cache_dir_for(
        URL, "v1", _bind_auth(URL, {"secret_ref": "FLUID_SCAFFOLD_B"})
    )

    assert len({plain.parent, with_a.parent, with_b.parent}) == 3
    expected = hashlib.sha256(f"{URL}\0github.com\0FLUID_SCAFFOLD_A".encode()).hexdigest()[:16]
    assert with_a.parent.name == expected  # url, host and variable name: no value in it


# ── the credential object ────────────────────────────────────────────────────


def test_a_credential_does_not_show_its_token_in_a_repr_or_in_text() -> None:
    credential = _Credential(url=URL, token=SECRET)

    assert SECRET not in repr(credential)
    redacted = credential.redact(f"bad: {SECRET} and {credential._basic}")
    assert SECRET not in redacted and credential._basic not in redacted


def test_the_header_is_bound_to_the_url_and_redirects_are_refused() -> None:
    credential = _Credential(url=URL, token=SECRET)

    keys = dict(credential.config())
    assert keys[f"http.{URL}.extraHeader"].startswith("Authorization: Basic ")
    assert keys["http.followRedirects"] == "false"


def test_config_pairs_follow_the_ones_the_caller_already_set() -> None:
    env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "me"}

    _add_git_config(env, [("a.b", "1"), ("c.d", "2")])

    assert env["GIT_CONFIG_COUNT"] == "3"
    assert (env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]) == ("user.name", "me")
    assert (env["GIT_CONFIG_KEY_1"], env["GIT_CONFIG_VALUE_1"]) == ("a.b", "1")
    assert (env["GIT_CONFIG_KEY_2"], env["GIT_CONFIG_VALUE_2"]) == ("c.d", "2")


def test_a_git_too_old_to_carry_the_credential_is_an_error(operator, monkeypatch) -> None:
    monkeypatch.setattr(git_resolver, "_git_version", lambda: (2, 30))

    with pytest.raises(ResolutionError, match=r"git 2\.31 or newer"):
        git_resolver._credential(URL, {"secret_ref": "FLUID_SCAFFOLD_TOKEN"})
