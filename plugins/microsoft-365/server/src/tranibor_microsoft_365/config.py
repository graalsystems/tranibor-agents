# SPDX-License-Identifier: Apache-2.0
"""Configuration, read from the environment once at start-up.

The server has no configuration of its own and no identity of its own: it reaches Microsoft Graph only
with the token of the person it is called for, which the platform sends in `X-Choregos-User-Token` on
each call (a `user_consent` connected account, the core's ADR 0135). It holds no credential, no client
secret and no storage.

`TRANIBOR_SERVER_TOKEN` is the platform's (ADR 0094): the bearer token it presents on every call. The
server refuses to start without it, except on the fake tenant with `TRANIBOR_SERVER_INSECURE_DEV=1` said
explicitly, for a local demo. `M365_FAKE=1` replaces Microsoft Graph with a seeded in-memory tenant, so
tests and demos need no account.

The core names the pod's variable `TRANIBOR_SERVER_TOKEN` since its `TRANIBOR_*` rename (core ADR 0176,
S75-04); an older core sets `CHOREGOS_SERVER_TOKEN`, still read when the new name is absent (and
`CHOREGOS_SERVER_INSECURE_DEV` likewise).
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, SecretStr

#: The platform's token variable, the current core's name first, then an older core's.
TOKEN_ENVS = ("TRANIBOR_SERVER_TOKEN", "CHOREGOS_SERVER_TOKEN")
INSECURE_DEV_ENVS = ("TRANIBOR_SERVER_INSECURE_DEV", "CHOREGOS_SERVER_INSECURE_DEV")


def _first(source: Mapping[str, str], names: tuple[str, ...]) -> str:
    """The value of the first of `names` set to something, else empty."""
    return next((source[name] for name in names if source.get(name)), "")


#: The only Graph the server calls. Fixed, not configurable: an administrator cannot point the person's
#: token at another host.
GRAPH_URL = "https://graph.microsoft.com/v1.0"


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    fake: bool = False
    port: int = 8080
    #: The bearer token the platform presents on every call but the health probe (ADR 0094).
    server_token: SecretStr | None = None
    #: `TRANIBOR_SERVER_INSECURE_DEV=1`: no token, accepted on the fake tenant only.
    insecure_dev: bool = False

    def require_server_token(self) -> SecretStr | None:
        """The token every call must carry; `None` only on the fake tenant in explicit insecure dev mode."""
        if self.server_token is not None and self.server_token.get_secret_value():
            return self.server_token
        if self.fake and self.insecure_dev:
            return None
        raise ValueError(
            "TRANIBOR_SERVER_TOKEN (or an older core's CHOREGOS_SERVER_TOKEN) is not set: the server refuses "
            "to start without the platform's token (only M365_FAKE=1 with TRANIBOR_SERVER_INSECURE_DEV=1 runs "
            "without it)"
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        token = _first(source, TOKEN_ENVS)
        return cls(
            fake=source.get("M365_FAKE", "") in ("1", "true", "yes"),
            port=int(source.get("PORT", "8080")),
            server_token=SecretStr(token) if token else None,
            insecure_dev=_first(source, INSECURE_DEV_ENVS) == "1",
        )
