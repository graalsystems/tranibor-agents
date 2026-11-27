# SPDX-License-Identifier: Apache-2.0
"""What every configuration here shares: the platform's token (ADR 0094), the instance's name, the fake switch.

`TRANIBOR_SERVER_TOKEN` is the bearer token the platform presents on every call. The server refuses to start
without it, except on the fake with `TRANIBOR_SERVER_INSECURE_DEV=1` said explicitly, for a local demo. An older
core sets `CHOREGOS_SERVER_TOKEN`, still read when the new name is absent (and `CHOREGOS_SERVER_INSECURE_DEV`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, SecretStr

#: The platform's token variable, the current core's name first, then an older core's.
TOKEN_ENVS = ("TRANIBOR_SERVER_TOKEN", "CHOREGOS_SERVER_TOKEN")
INSECURE_DEV_ENVS = ("TRANIBOR_SERVER_INSECURE_DEV", "CHOREGOS_SERVER_INSECURE_DEV")
#: Set by whoever installs the server (or the platform, when it does): how this instance names itself.
INSTANCE_ENV = "TRANIBOR_INSTANCE_NAME"

LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
HOSTNAME = re.compile(rf"^{LABEL}(\.{LABEL})*$")


def first(source: Mapping[str, str], names: tuple[str, ...]) -> str:
    """The value of the first of `names` set to something, else empty."""
    return next((source[name] for name in names if source.get(name)), "")


def split(value: str) -> tuple[str, ...]:
    """A list field as the platform delivers it: items joined by commas (or newlines)."""
    return tuple(part.strip() for part in re.split(r"[,\n]", value) if part.strip())


class CommonSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: This instance's name: its connector's name in the platform (`get_provider` says it).
    instance: str
    fake: bool = False
    port: int = 8080
    #: The bearer token the platform presents on every call but the health probe (ADR 0094).
    server_token: SecretStr | None = None
    #: `TRANIBOR_SERVER_INSECURE_DEV=1`: no token, accepted on the fake only.
    insecure_dev: bool = False

    def require_server_token(self) -> SecretStr | None:
        """The token every call must carry; `None` only on the fake in explicit insecure dev mode."""
        if self.server_token is not None and self.server_token.get_secret_value():
            return self.server_token
        if self.fake and self.insecure_dev:
            return None
        raise ValueError(
            "TRANIBOR_SERVER_TOKEN (or an older core's CHOREGOS_SERVER_TOKEN) is not set: the server refuses to "
            "start without the platform's token (only the fake with TRANIBOR_SERVER_INSECURE_DEV=1 runs without it)"
        )


def common(source: Mapping[str, str], default_instance: str) -> dict[str, object]:
    token = first(source, TOKEN_ENVS)
    return {
        "instance": source.get(INSTANCE_ENV, "") or default_instance,
        "port": int(source.get("PORT", "8080")),
        "server_token": SecretStr(token) if token else None,
        "insecure_dev": first(source, INSECURE_DEV_ENVS) == "1",
    }
