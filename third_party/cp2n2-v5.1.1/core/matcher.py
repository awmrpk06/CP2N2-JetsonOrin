"""Admission, feasibility, and selection for CP²N² resources."""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field

from core.task_model import SelectionPolicy, TaskRequest
from descriptors.capability_schema import Locality, SubstrateDescriptor


RuntimeValue = float | int | str | bool | None


class DecisionStage(str, Enum):
    """The three non-interchangeable stages of resource selection."""

    ADMISSION = "admission"
    FEASIBILITY = "feasibility"
    RANKING = "ranking"


class ConstraintDecision(BaseModel):
    """Machine-readable result of a hard-constraint stage."""

    model_config = ConfigDict(extra="forbid")

    stage: DecisionStage
    passed: bool
    satisfied_constraints: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)


class RankingDecision(BaseModel):
    """Transparent ranking data for an admitted and feasible candidate."""

    model_config = ConfigDict(extra="forbid")

    policy: SelectionPolicy
    rank: int | None = Field(default=None, ge=1)
    rank_key: list[float | int | str] = Field(default_factory=list)
    criteria: dict[str, RuntimeValue] = Field(default_factory=dict)
    weighted_components: dict[str, float] = Field(default_factory=dict)
    weighted_score: float = 0.0


class MatchCandidate(BaseModel):
    """One candidate with separate admission, feasibility, and ranking evidence."""

    model_config = ConfigDict(extra="forbid")

    backend_id: str
    display_name: str
    accepted: bool
    score: float = Field(
        default=0.0,
        description="Compatibility field containing the explicit weighted-comparison score.",
    )
    admission: ConstraintDecision
    feasibility: ConstraintDecision
    ranking: RankingDecision
    reasons: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)

    def explanation_lines(self) -> list[str]:
        """Return a compact human-readable rendering of the decision record."""
        rank = "-" if self.ranking.rank is None else str(self.ranking.rank)
        lines = [
            (
                f"[{self.backend_id}] accepted={self.accepted} "
                f"policy={self.ranking.policy} rank={rank} "
                f"weighted_comparison={self.score:.2f}"
            )
        ]
        lines.extend(f"  + {reason}" for reason in self.reasons)
        lines.extend(f"  - {reason}" for reason in self.rejection_reasons)
        return lines


class MatchReport(BaseModel):
    """Complete, reproducible selection report for a task."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    policy: SelectionPolicy
    candidates: list[MatchCandidate] = Field(default_factory=list)

    def accepted_candidates(self) -> list[MatchCandidate]:
        """Return admitted and feasible candidates in policy order."""
        return [candidate for candidate in self.candidates if candidate.accepted]

    def best_candidate(self) -> MatchCandidate | None:
        """Return the first admitted and feasible candidate, if any."""
        accepted = self.accepted_candidates()
        return accepted[0] if accepted else None


class BackendMatcher:
    """Three-stage, substrate-neutral resource selector.

    Hard task/policy checks are admission decisions. Dynamic availability and
    budget checks are feasibility decisions. Only candidates that pass both
    stages reach ranking. The principal policy is lexicographic; the historical
    weighted heuristic remains explicit and available only for comparison.
    """

    DRIFT_INADMISSIBLE_THRESHOLD = 0.95

    # These are the v3.0 heuristic weights, made explicit for reproducibility.
    WEIGHTED_HEURISTIC: dict[str, float] = {
        "base_accepted": 50.0,
        "task_supported": 20.0,
        "modality_overlap": 15.0,
        "latency_within_budget": 20.0,
        "low_variability_match": 6.0,
        "low_variability_mismatch": -8.0,
        "stochastic_tolerated": 2.0,
        "continuous_health_telemetry": 8.0,
        "continuous_drift_telemetry": 4.0,
        "required_telemetry_each": 2.0,
        "age_of_information_supported": 6.0,
        "reset_free_match": 4.0,
        "reset_free_mismatch": -8.0,
        "locality_match": 6.0,
        "health_sensitive": -5.0,
        "health_ready": 4.0,
        "health_degraded": -20.0,
        "low_drift": 6.0,
        "medium_drift_multiplier": -12.0,
        "freshness_comfortable": 4.0,
        "freshness_ratio_multiplier": -6.0,
    }

    def __init__(
        self,
        *,
        weighted_overrides: dict[str, float] | None = None,
        static_priority: list[str] | None = None,
    ) -> None:
        self._weights = dict(self.WEIGHTED_HEURISTIC)
        if weighted_overrides:
            unknown = sorted(set(weighted_overrides) - set(self._weights))
            if unknown:
                raise ValueError("Unknown weighted heuristic terms: " + ", ".join(unknown))
            self._weights.update(weighted_overrides)
        self._static_priority = list(static_priority or [])

    @property
    def weighted_heuristic(self) -> dict[str, float]:
        """Return an isolated copy of the comparison weights."""
        return dict(self._weights)

    def rank_backends(
        self,
        task: TaskRequest,
        descriptors: Iterable[SubstrateDescriptor],
        runtime_state: dict[str, dict[str, RuntimeValue]] | None = None,
        *,
        policy: SelectionPolicy | str | None = None,
    ) -> MatchReport:
        """Run admission, feasibility, and then ranking for every resource."""
        selected_policy = SelectionPolicy(policy or task.selection_policy)
        state = runtime_state or {}
        candidates = [
            self.evaluate_descriptor(
                task,
                descriptor,
                runtime_state=state.get(descriptor.backend_id, {}),
                policy=selected_policy,
            )
            for descriptor in descriptors
        ]

        eligible = [candidate for candidate in candidates if candidate.accepted]
        excluded = [candidate for candidate in candidates if not candidate.accepted]
        eligible.sort(key=lambda item: self._sort_key(item, task, selected_policy))
        excluded.sort(key=lambda item: item.backend_id)

        for rank, candidate in enumerate(eligible, start=1):
            candidate.ranking.rank = rank
        return MatchReport(
            task_id=task.task_id,
            policy=selected_policy,
            candidates=eligible + excluded,
        )

    def score_descriptor(
        self,
        task: TaskRequest,
        descriptor: SubstrateDescriptor,
        runtime_state: dict[str, RuntimeValue] | None = None,
    ) -> MatchCandidate:
        """Compatibility alias for callers of the pre-A3 matcher API."""
        return self.evaluate_descriptor(
            task,
            descriptor,
            runtime_state=runtime_state,
            policy=SelectionPolicy(task.selection_policy),
        )

    def evaluate_descriptor(
        self,
        task: TaskRequest,
        descriptor: SubstrateDescriptor,
        runtime_state: dict[str, RuntimeValue] | None = None,
        *,
        policy: SelectionPolicy = SelectionPolicy.LEXICOGRAPHIC,
    ) -> MatchCandidate:
        """Produce the complete three-stage record for one backend."""
        runtime = runtime_state or {}
        admission = self._assess_admission(task, descriptor, runtime)
        feasibility = self._assess_feasibility(task, descriptor, runtime, admission.passed)
        accepted = admission.passed and feasibility.passed
        criteria = self._ranking_criteria(task, descriptor, runtime)
        weighted_components = self._weighted_components(task, descriptor, runtime)
        weighted_score = max(sum(weighted_components.values()), 0.0) if accepted else 0.0
        if accepted and policy == SelectionPolicy.WEIGHTED_COMPARISON:
            rank_key: tuple[float | int | str, ...] = (
                -weighted_score,
                descriptor.backend_id,
            )
        elif accepted:
            rank_key = self._policy_rank_key(
                policy,
                criteria,
                descriptor.backend_id,
                task,
            )
        else:
            rank_key = ()
        ranking = RankingDecision(
            policy=policy,
            rank_key=list(rank_key),
            criteria=criteria,
            weighted_components=weighted_components if accepted else {},
            weighted_score=weighted_score,
        )
        rejection_reasons = admission.violations + feasibility.violations
        reasons = (
            admission.satisfied_constraints
            + feasibility.satisfied_constraints
            + (
                [f"Ranking policy '{policy.value}' applied only after hard checks."]
                if accepted
                else []
            )
        )
        return MatchCandidate(
            backend_id=descriptor.backend_id,
            display_name=descriptor.display_name,
            accepted=accepted,
            score=weighted_score,
            admission=admission,
            feasibility=feasibility,
            ranking=ranking,
            reasons=reasons,
            rejection_reasons=rejection_reasons,
        )

    def _assess_admission(
        self,
        task: TaskRequest,
        descriptor: SubstrateDescriptor,
        runtime: dict[str, RuntimeValue],
    ) -> ConstraintDecision:
        satisfied: list[str] = []
        violations: list[str] = []
        task_type = task.normalized_task_type()
        supported_modalities = {str(item.modality) for item in descriptor.input_contracts}
        required_modalities = {str(item) for item in task.required_input_modalities}
        declared_telemetry = self._declared_telemetry_names(descriptor)

        self._record(
            task.direct_backend_id is None or descriptor.backend_id == task.direct_backend_id,
            "direct_backend_target",
            (
                f"Task targets '{task.direct_backend_id}', not "
                f"'{descriptor.backend_id}'."
            ),
            satisfied,
            violations,
        )
        self._record(
            descriptor.supports_task_type(task_type),
            "supported_task_type",
            f"Task type '{task_type}' is not supported.",
            satisfied,
            violations,
        )
        self._record(
            bool(required_modalities.intersection(supported_modalities)),
            "input_modality_compatible",
            "No required input modality is supported.",
            satisfied,
            violations,
        )
        self._record(
            not task.repeated_invocation_expected
            or descriptor.capability.repeated_invocation_supported,
            "repeated_invocation_policy",
            "Repeated invocation is required but not supported.",
            satisfied,
            violations,
        )
        missing = sorted(set(task.required_telemetry_fields) - declared_telemetry)
        self._record(
            not missing,
            "required_telemetry_declared",
            "Required telemetry is not declared: " + ", ".join(missing),
            satisfied,
            violations,
        )
        self._record(
            task.max_twin_age_ms is None
            or descriptor.telemetry.supports_age_of_information,
            "freshness_observable",
            "The task requires age-of-information but the backend cannot expose it.",
            satisfied,
            violations,
        )
        self._record(
            not descriptor.policy.human_supervision_required
            or task.human_supervision_available,
            "human_supervision_policy",
            "Human supervision is required but unavailable.",
            satisfied,
            violations,
        )
        self._record(
            not task.continuous_monitoring_required
            or descriptor.telemetry.supports_health_status,
            "continuous_monitoring_capability",
            "Continuous monitoring requires health-status telemetry.",
            satisfied,
            violations,
        )
        self._record(
            task.min_confidence <= 0.0 or descriptor.telemetry.supports_confidence,
            "confidence_observable",
            "A confidence threshold is requested but confidence is not exposed.",
            satisfied,
            violations,
        )

        safety_admissible = runtime.get("contract_safety_admissible")
        if isinstance(safety_admissible, bool):
            safety_reasons = runtime.get("contract_safety_reason")
            self._record(
                safety_admissible,
                "resource_contract_safety",
                str(safety_reasons or "The resource safety contract is inadmissible."),
                satisfied,
                violations,
            )

        return ConstraintDecision(
            stage=DecisionStage.ADMISSION,
            passed=not violations,
            satisfied_constraints=satisfied,
            violations=violations,
        )

    def _assess_feasibility(
        self,
        task: TaskRequest,
        descriptor: SubstrateDescriptor,
        runtime: dict[str, RuntimeValue],
        admitted: bool,
    ) -> ConstraintDecision:
        if not admitted:
            return ConstraintDecision(
                stage=DecisionStage.FEASIBILITY,
                passed=False,
                violations=["Feasibility was not evaluated because admission failed."],
            )

        satisfied: list[str] = []
        violations: list[str] = []
        health = self._normalise(runtime.get("health_status"), "unknown")
        lifecycle = self._normalise(runtime.get("control_plane_lifecycle"), "ready")
        reservable = runtime.get("reservable")
        drift = self._number(runtime.get("drift_score"))
        age = self._number(runtime.get("age_of_information_ms"))
        estimated_cost = self._number(runtime.get("estimated_cost"))
        currency = self._normalise(runtime.get("cost_currency"), "")

        self._record(
            health not in {"offline", "failed", "unreachable"},
            "runtime_available",
            f"Runtime health is '{health}'.",
            satisfied,
            violations,
        )
        self._record(
            lifecycle not in {"failed", "unreachable", "aborting"},
            "lifecycle_available",
            f"Control-plane lifecycle is '{lifecycle}'.",
            satisfied,
            violations,
        )
        if isinstance(reservable, bool):
            self._record(
                reservable,
                "resource_reservable",
                "The resource cannot currently be reserved by this client.",
                satisfied,
                violations,
            )
        if task.required_telemetry_fields:
            missing_runtime = sorted(
                name
                for name in task.required_telemetry_fields
                if name not in runtime or runtime[name] is None
            )
            self._record(
                not missing_runtime,
                "required_telemetry_available",
                "Required runtime telemetry is unavailable: "
                + ", ".join(missing_runtime),
                satisfied,
                violations,
            )
        self._record(
            descriptor.timing.typical_latency_ms <= task.latency_budget_ms,
            "latency_budget",
            (
                f"Typical latency {descriptor.timing.typical_latency_ms:.2f} ms "
                f"exceeds budget {task.latency_budget_ms:.2f} ms."
            ),
            satisfied,
            violations,
        )
        if drift is not None:
            self._record(
                drift < self.DRIFT_INADMISSIBLE_THRESHOLD,
                "drift_threshold",
                (
                    f"Runtime drift_score {drift:.2f} meets or exceeds "
                    f"{self.DRIFT_INADMISSIBLE_THRESHOLD:.2f}."
                ),
                satisfied,
                violations,
            )
        if task.max_twin_age_ms is not None:
            self._record(
                age is not None and age <= task.max_twin_age_ms,
                "telemetry_freshness",
                (
                    "Runtime age_of_information_ms is unavailable."
                    if age is None
                    else (
                        f"Runtime age_of_information_ms {age:.2f} exceeds "
                        f"{task.max_twin_age_ms:.2f}."
                    )
                ),
                satisfied,
                violations,
            )
        if task.max_estimated_cost is not None:
            currency_matches = currency == str(task.cost_currency).lower()
            self._record(
                estimated_cost is not None
                and currency_matches
                and estimated_cost <= task.max_estimated_cost,
                "cost_budget",
                (
                    "Estimated cost is unavailable or has a different currency."
                    if estimated_cost is None or not currency_matches
                    else (
                        f"Estimated cost {estimated_cost:.4f} {task.cost_currency} "
                        f"exceeds {task.max_estimated_cost:.4f}."
                    )
                ),
                satisfied,
                violations,
            )

        return ConstraintDecision(
            stage=DecisionStage.FEASIBILITY,
            passed=not violations,
            satisfied_constraints=satisfied,
            violations=violations,
        )

    def _ranking_criteria(
        self,
        task: TaskRequest,
        descriptor: SubstrateDescriptor,
        runtime: dict[str, RuntimeValue],
    ) -> dict[str, RuntimeValue]:
        health = self._normalise(runtime.get("health_status"), "unknown")
        drift = self._number(runtime.get("drift_score"))
        age = self._number(runtime.get("age_of_information_ms"))
        estimated_cost = self._number(runtime.get("estimated_cost"))
        locality_distance = (
            0.0
            if task.preferred_locality is None
            else self._locality_penalty(
                str(task.preferred_locality),
                str(descriptor.policy.locality),
            )
        )
        freshness_ratio = (
            age / task.max_twin_age_ms
            if age is not None and task.max_twin_age_ms is not None
            else (0.0 if age is not None else 1.0)
        )
        return {
            "health_penalty": {
                "ready": 0.0,
                "healthy": 0.0,
                "degraded": 1.0,
                "unknown": 2.0,
            }.get(health, 3.0),
            "health_sensitive_penalty": 1.0 if descriptor.capability.health_sensitive else 0.0,
            "drift_score": drift if drift is not None else 1.0,
            "freshness_ratio": freshness_ratio,
            "latency_ratio": descriptor.timing.typical_latency_ms / task.latency_budget_ms,
            "typical_latency_ms": descriptor.timing.typical_latency_ms,
            "locality_distance": locality_distance,
            "cost_unknown": estimated_cost is None,
            "estimated_cost": estimated_cost if estimated_cost is not None else 0.0,
            "variability_mismatch": (
                1.0
                if task.prefers_low_variability() and descriptor.capability.stochastic
                else 0.0
            ),
            "reset_mismatch": (
                1.0
                if task.reset_free_preferred
                and descriptor.lifecycle.stateful
                and bool(descriptor.lifecycle.supported_reset_modes)
                else 0.0
            ),
        }

    def _policy_rank_key(
        self,
        policy: SelectionPolicy,
        criteria: dict[str, RuntimeValue],
        backend_id: str,
        task: TaskRequest,
    ) -> tuple[float | int | str, ...]:
        latency = float(criteria["latency_ratio"])
        safety = (
            float(criteria["health_penalty"]),
            float(criteria["health_sensitive_penalty"]),
            float(criteria["drift_score"]),
            float(criteria["freshness_ratio"]),
        )
        locality_cost = (
            float(criteria["locality_distance"]),
            int(bool(criteria["cost_unknown"])),
            float(criteria["estimated_cost"]),
        )
        quality = (
            float(criteria["variability_mismatch"]),
            float(criteria["reset_mismatch"]),
        )

        if policy == SelectionPolicy.LATENCY_FIRST:
            return (latency, *safety, *locality_cost, *quality, backend_id)
        if policy == SelectionPolicy.SAFETY_FRESHNESS_FIRST:
            return (*safety, latency, *locality_cost, *quality, backend_id)
        if policy == SelectionPolicy.LOCALITY_COST_FIRST:
            return (*locality_cost, *safety, latency, *quality, backend_id)
        if policy == SelectionPolicy.WEIGHTED_COMPARISON:
            return (backend_id,)
        if policy == SelectionPolicy.STATIC_PRIORITY:
            try:
                priority = self._static_priority.index(backend_id)
            except ValueError:
                priority = len(self._static_priority)
            return (priority, backend_id)
        if policy == SelectionPolicy.CONSTRAINT_BASED:
            return (backend_id,)
        if policy == SelectionPolicy.RANDOM_ADMISSIBLE:
            digest = hashlib.sha256(
                f"{task.selection_seed}:{task.task_id}:{backend_id}".encode("utf-8")
            ).hexdigest()
            return (digest, backend_id)
        # Principal policy: safety/freshness, then quality, latency, locality/cost.
        return (*safety, *quality, latency, *locality_cost, backend_id)

    def _sort_key(
        self,
        candidate: MatchCandidate,
        task: TaskRequest,
        policy: SelectionPolicy,
    ) -> tuple:
        if policy == SelectionPolicy.WEIGHTED_COMPARISON:
            return (-candidate.ranking.weighted_score, candidate.backend_id)
        return tuple(candidate.ranking.rank_key)

    def _weighted_components(
        self,
        task: TaskRequest,
        descriptor: SubstrateDescriptor,
        runtime: dict[str, RuntimeValue],
    ) -> dict[str, float]:
        weights = self._weights
        components = {
            "base_accepted": weights["base_accepted"],
            "task_supported": weights["task_supported"],
            "modality_overlap": weights["modality_overlap"],
            "latency_within_budget": weights["latency_within_budget"],
        }
        if task.prefers_low_variability():
            components["variability"] = (
                weights["low_variability_mismatch"]
                if descriptor.capability.stochastic
                else weights["low_variability_match"]
            )
        elif descriptor.capability.stochastic:
            components["stochastic_tolerated"] = weights["stochastic_tolerated"]
        if task.continuous_monitoring_required:
            components["continuous_health_telemetry"] = weights[
                "continuous_health_telemetry"
            ]
            if descriptor.telemetry.supports_drift_reporting:
                components["continuous_drift_telemetry"] = weights[
                    "continuous_drift_telemetry"
                ]
        if task.required_telemetry_fields:
            components["required_telemetry"] = (
                weights["required_telemetry_each"]
                * len(task.required_telemetry_fields)
            )
        if task.max_twin_age_ms is not None:
            components["age_of_information_supported"] = weights[
                "age_of_information_supported"
            ]
        if task.reset_free_preferred:
            components["reset_preference"] = (
                weights["reset_free_mismatch"]
                if descriptor.lifecycle.stateful
                and descriptor.lifecycle.supported_reset_modes
                else weights["reset_free_match"]
            )
        if task.preferred_locality is not None:
            locality_penalty = self._locality_penalty(
                str(task.preferred_locality),
                str(descriptor.policy.locality),
            )
            components["locality"] = (
                weights["locality_match"] if locality_penalty == 0 else -locality_penalty
            )
        if descriptor.capability.health_sensitive:
            components["health_sensitive"] = weights["health_sensitive"]

        health = self._normalise(runtime.get("health_status"), "unknown")
        if health == "ready":
            components["health_ready"] = weights["health_ready"]
        elif health == "degraded":
            components["health_degraded"] = weights["health_degraded"]
        drift = self._number(runtime.get("drift_score"))
        if drift is not None:
            if drift <= 0.25:
                components["low_drift"] = weights["low_drift"]
            elif drift <= 0.75:
                components["medium_drift"] = weights["medium_drift_multiplier"] * drift
        age = self._number(runtime.get("age_of_information_ms"))
        if age is not None and task.max_twin_age_ms is not None:
            ratio = age / task.max_twin_age_ms
            components["freshness"] = (
                weights["freshness_comfortable"]
                if ratio <= 0.5
                else weights["freshness_ratio_multiplier"] * ratio
            )
        return components

    @staticmethod
    def _record(
        passed: bool,
        name: str,
        violation: str,
        satisfied: list[str],
        violations: list[str],
    ) -> None:
        if passed:
            satisfied.append(name)
        else:
            violations.append(f"{name}: {violation}")

    @staticmethod
    def _number(value: RuntimeValue) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    @staticmethod
    def _normalise(value: RuntimeValue, default: str) -> str:
        return default if value is None else str(value).strip().lower()

    @staticmethod
    def _declared_telemetry_names(descriptor: SubstrateDescriptor) -> set[str]:
        names = {field.name for field in descriptor.telemetry.metrics}
        if descriptor.telemetry.supports_health_status:
            names.add("health_status")
        if descriptor.telemetry.supports_confidence:
            names.update({"confidence", "last_confidence", "calibration_confidence"})
        if descriptor.telemetry.supports_drift_reporting:
            names.add("drift_score")
        if descriptor.telemetry.supports_age_of_information:
            names.add("age_of_information_ms")
        return names

    @staticmethod
    def _locality_penalty(requested: str, available: str) -> float:
        """Return the documented locality distance used by two policies."""
        if requested == available:
            return 0.0
        if requested == str(Locality.EDGE) and available in {
            str(Locality.FOG),
            str(Locality.LOCAL),
        }:
            return 3.0
        if requested == str(Locality.FOG) and available in {
            str(Locality.EDGE),
            str(Locality.CLOUD),
            str(Locality.LOCAL),
        }:
            return 3.0
        if requested == str(Locality.CLOUD) and available in {
            str(Locality.FOG),
            str(Locality.LOCAL),
        }:
            return 2.0
        if requested == str(Locality.LAB) and available == str(Locality.LOCAL):
            return 2.0
        return 6.0
