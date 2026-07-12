"""Configuration management for Nutanix AIops.

Loads Prism Central connection targets from a YAML config file. The secret (the
Prism Central account **password**) is NEVER stored in the config file and never
on disk in plaintext: it lives in the encrypted store
``~/.nutanix-aiops/secrets.enc`` (see :mod:`nutanix_aiops.secretstore`). For
backward compatibility a legacy plaintext env var (``NUTANIX_<TARGET>_PASSWORD``)
is still honoured as a fallback, with a warning nudging migration to the
encrypted store.

A "target" is one **Prism Central** (PC) instance. Prism Central listens on
HTTPS port ``9440`` and the v4 REST APIs authenticate with HTTP Basic auth
(``username`` + password). ``username`` lives in the config file (it is not a
secret); the password lives encrypted.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from nutanix_aiops.secretstore import SecretStoreError, get_secret, has_store

CONFIG_DIR = Path.home() / ".nutanix-aiops"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
ENV_FILE = CONFIG_DIR / ".env"

# Prism Central defaults.
DEFAULT_PC_PORT = 9440
DEFAULT_USERNAME = "admin"

# Legacy env-var prefix/suffix; also used by the migration helper.
SECRET_ENV_PREFIX = "NUTANIX_"  # nosec B105 — env-var name, not a secret
SECRET_ENV_SUFFIX = "_PASSWORD"  # nosec B105 — env-var name, not a secret

_log = logging.getLogger("nutanix-aiops.config")


def _secret_env_key(name: str) -> str:
    """Legacy per-target password env var name, e.g. NUTANIX_PC1_PASSWORD."""
    return f"{SECRET_ENV_PREFIX}{name.upper().replace('-', '_')}{SECRET_ENV_SUFFIX}"


def _resolve_secret(name: str) -> str:
    """Return a target's password: encrypted store first, then legacy env var."""
    if has_store():
        try:
            return get_secret(name)
        except SecretStoreError:
            pass  # fall through to legacy env var
    legacy = os.environ.get(_secret_env_key(name))
    if legacy:
        _log.warning(
            "Using plaintext env var %s. Migrate to the encrypted store with "
            "'nutanix-aiops secret migrate'.",
            _secret_env_key(name),
        )
        return legacy
    raise OSError(
        f"No password for target '{name}'. Add one with "
        f"'nutanix-aiops secret set {name}' (stored encrypted), or run "
        f"'nutanix-aiops init'."
    )


@dataclass(frozen=True)
class TargetConfig:
    """A connection target for one Prism Central instance.

    The password is sourced from the encrypted secret store (see ``password``),
    never the config file. ``host`` is the Prism Central IP/FQDN; ``port``
    defaults to ``9440``; ``username`` (default ``admin``) is the PC account and
    is not a secret.
    """

    name: str
    host: str
    port: int = DEFAULT_PC_PORT
    username: str = DEFAULT_USERNAME
    verify_ssl: bool = True

    @property
    def password(self) -> str:
        return _resolve_secret(self.name)

    @property
    def base_url(self) -> str:
        # v4 APIs live under per-namespace paths (e.g. /api/vmm/v4.0/...), so the
        # base URL is just the PC origin; callers pass the full /api path.
        return f"https://{self.host}:{self.port}"


@dataclass(frozen=True)
class AppConfig:
    """Top-level application config."""

    targets: tuple[TargetConfig, ...] = ()

    def get_target(self, name: str) -> TargetConfig:
        for t in self.targets:
            if t.name == name:
                return t
        available = ", ".join(t.name for t in self.targets) or "(none)"
        raise KeyError(f"Target '{name}' not found. Available: {available}")

    @property
    def default_target(self) -> TargetConfig:
        if not self.targets:
            raise ValueError("No targets configured. Check config.yaml")
        return self.targets[0]


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load config from YAML; the password comes from the encrypted store."""
    path = config_path or CONFIG_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Run 'nutanix-aiops init' to set up a Prism Central target and store "
            f"its password encrypted, or create {CONFIG_FILE} with a 'targets' list."
        )

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    targets = tuple(
        TargetConfig(
            name=t["name"],
            host=t["host"],
            port=t.get("port", DEFAULT_PC_PORT),
            username=t.get("username", DEFAULT_USERNAME),
            verify_ssl=t.get("verify_ssl", True),
        )
        for t in raw.get("targets", [])
    )

    return AppConfig(targets=targets)
