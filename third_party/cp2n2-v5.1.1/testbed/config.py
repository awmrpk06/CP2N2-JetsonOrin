"""Versioned configuration models for the A6 distributed testbed."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


TESTBED_SCHEMA_VERSION = "1.0"


class LinkProfile(BaseModel):
    """Deterministic application-layer impairment profile for one link."""

    model_config = ConfigDict(extra="forbid")

    latency_ms: float = Field(default=0.0, ge=0.0)
    jitter_ms: float = Field(default=0.0, ge=0.0)
    loss_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    partition_every_n_requests: int | None = Field(default=None, ge=1)
    partition_duration_ms: float = Field(default=0.0, ge=0.0)


class NetworkProfile(BaseModel):
    """One versioned, seeded network and telemetry fault profile."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = TESTBED_SCHEMA_VERSION
    profile_id: str
    description: str
    seed: int = 7
    agent_gateway: LinkProfile = Field(default_factory=LinkProfile)
    gateway_control: LinkProfile = Field(default_factory=LinkProfile)
    control_adapter: LinkProfile = Field(default_factory=LinkProfile)
    telemetry_staleness_ms: float = Field(default=0.0, ge=0.0)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != TESTBED_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported testbed schema_version {value!r}; "
                f"expected {TESTBED_SCHEMA_VERSION!r}"
            )
        return value

    @field_validator("profile_id", "description")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("profile_id and description must not be empty")
        return value


class ServiceConfiguration(BaseModel):
    """One independently launchable service in a deployment."""

    model_config = ConfigDict(extra="forbid")

    service_id: str
    module: str
    host: str = "127.0.0.1"
    port: int | None = Field(default=None, ge=1, le=65535)
    depends_on: list[str] = Field(default_factory=list)


class DeploymentConfiguration(BaseModel):
    """Versioned process/host topology."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = TESTBED_SCHEMA_VERSION
    deployment_id: str
    description: str
    services: list[ServiceConfiguration]

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != TESTBED_SCHEMA_VERSION:
            raise ValueError("unsupported deployment schema version")
        return value

    @model_validator(mode="after")
    def validate_topology(self) -> "DeploymentConfiguration":
        identifiers = [service.service_id for service in self.services]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("service_id values must be unique")
        known = set(identifiers)
        for service in self.services:
            unknown = set(service.depends_on) - known
            if unknown:
                raise ValueError(
                    f"{service.service_id} has unknown dependencies: "
                    + ", ".join(sorted(unknown))
                )
        return self


class CampaignConfiguration(BaseModel):
    """Versioned matrix for automated RQ2 experiments."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = TESTBED_SCHEMA_VERSION
    campaign_id: str
    deployment: str
    profiles: list[str]
    client_counts: list[int]
    repetitions: int = Field(default=3, ge=1)
    request_timeout_s: float = Field(default=10.0, gt=0.0)
    seed: int = 7

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != TESTBED_SCHEMA_VERSION:
            raise ValueError("unsupported campaign schema version")
        return value

    @field_validator("client_counts")
    @classmethod
    def validate_client_counts(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("client_counts must not be empty")
        normalized = sorted(set(value))
        if normalized[0] < 1 or normalized[-1] > 32:
            raise ValueError("client_counts must stay within the A6 range 1..32")
        return normalized

    @field_validator("profiles")
    @classmethod
    def validate_profiles(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not normalized:
            raise ValueError("profiles must not be empty")
        return normalized


def _load(path: str | Path, model_type):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return model_type.model_validate(payload)


def load_network_profile(path: str | Path) -> NetworkProfile:
    return _load(path, NetworkProfile)


def load_deployment_configuration(path: str | Path) -> DeploymentConfiguration:
    return _load(path, DeploymentConfiguration)


def load_campaign_configuration(path: str | Path) -> CampaignConfiguration:
    return _load(path, CampaignConfiguration)
