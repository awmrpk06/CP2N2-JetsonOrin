"""Run the full evaluation suite and refresh result artefacts."""

from __future__ import annotations

from evaluation.agent_to_pnn_campaign import (
    ReferenceCampaignPlanner,
    run_campaign as run_agent_to_pnn_campaign,
)
from evaluation.common import RESULTS_DIR
from evaluation.evaluate_externalized_backend import evaluate as evaluate_externalized_backend
from evaluation.evaluate_distributed_testbed import evaluate as evaluate_distributed_testbed
from evaluation.evaluate_failure_campaign import evaluate as evaluate_failure_campaign
from evaluation.evaluate_matching import evaluate as evaluate_matching
from evaluation.evaluate_matching_baselines import evaluate as evaluate_matching_baselines
from evaluation.evaluate_overhead import evaluate as evaluate_overhead
from evaluation.evaluate_portability import evaluate as evaluate_portability
from evaluation.evaluate_selection_robustness import (
    evaluate as evaluate_selection_robustness,
)


def main() -> None:
    run_agent_to_pnn_campaign(
        planner=ReferenceCampaignPlanner(),
        output_dir=RESULTS_DIR / "agent-to-pnn-reference",
    )
    evaluate_overhead()
    evaluate_portability()
    evaluate_matching()
    evaluate_matching_baselines()
    evaluate_selection_robustness()
    evaluate_failure_campaign()
    evaluate_externalized_backend()
    evaluate_distributed_testbed()
    print("All evaluation artefacts refreshed.")


if __name__ == "__main__":
    main()
