"""Provider-shell configuration exposing no physical control primitive."""

from typing import Annotated, Literal, override

from cl.app import BaseApplicationConfig
from pydantic import Field


CONFIG_SHA256 = "5fccaac3022e223fc181508833eaad38755279556b43b6b2df4e2f7e032a08e4"
DECODER_SHA256 = "42789a20ea16e048f1a23b28e601ff3445e64b125c48de8656b11e612991afbf"


class BioPatternGateApplicationConfig(BaseApplicationConfig):
    """Exact E3 package selection; hardware presets are intentionally absent."""

    preset_id: Literal["technical-e3"] = "technical-e3"
    config_sha256: Literal[CONFIG_SHA256] = CONFIG_SHA256
    decoder_sha256: Literal[DECODER_SHA256] = DECODER_SHA256
    run_id: Annotated[
        str,
        Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
    ] = "cl-local-technical-e3"

    @override
    def estimate_duration_s(self) -> float:
        return 3.0
