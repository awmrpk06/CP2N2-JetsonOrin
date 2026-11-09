# University of Lübeck AI-Lab agent

## Role and authority boundary

The AI-Lab integration is an additional LLM planning runtime for CP²N². It
does not replace the generic architecture or the Gemini and Ollama examples.
It receives exactly the same sanitized discovery view, produces the same
strict `AgentPlan`, and executes only through `ConstrainedAgentExecutor` and
the ten-tool A4 MCP surface.

The model cannot provide physical control parameters, mutate policies, select
an evidence label, bypass leases, mint human approval, call a backend API, or
perform repeated execution loops. A model response with any additional field
is rejected before a state-changing MCP call.

## Provider contract confirmed on 2026-07-24

Sources were read from AI-Lab documentation version 1.4.1 through the
University network/VPN:

- documentation: `https://docs.ai-lab.uni-luebeck.de`;
- API: `https://llm-api.ai-lab.uni-luebeck.de`;
- implementation: LiteLLM with an OpenAI-compatible API;
- authentication: personal bearer key beginning with `sk-`;
- model discovery: `GET /v1/models`;
- chat completion: `POST /v1/chat/completions`;
- budget status: `GET /user/info`;
- allocation: budget units with a weekly reset;
- explicit RPM/TPM limits: not published in documentation version 1.4.1.

The documented model catalog at verification time includes
`deepseek-v4-flash`, `gemma-4-26b-a4b-it`, `qwen3.6-27b`,
`minimax-m2.7`, `gpt-oss-120b`, `qwen3-vl-8b-instruct`, and `bge-m3`.
The default is `minimax-m2.7`, the model used in the provider's API example
and described as suitable for agent and code tasks. The live evaluation first
queries `/v1/models` and refuses to run if the configured model is absent.

Models are periodically changed by the provider. Evaluation outputs therefore
record the requested model, returned model identifiers, request IDs, token
usage when supplied, and a SHA-256 digest of the current model catalog.

## Data handling and acceptable use

Provider policy restricts the service to research and education. Personal
data, patient data, and confidential university documents must not be sent to
the models. The policy also prohibits extremist, sexually explicit, criminal,
harmful, and misinformation-related uses.

The CP²N² evaluation sends only synthetic resource descriptors, fixed
server-owned preset identifiers, and generic goals. It contains no human
subject data, patient data, unpublished substrate recordings, credentials, or
confidential university documents.

## Credential configuration

Create an untracked `.env` file in the repository root:

```text
AI_LAB_API_KEY=sk-...
AI_LAB_BASE_URL=https://llm-api.ai-lab.uni-luebeck.de
AI_LAB_MODEL=minimax-m2.7
```

`LITELLM_API_KEY` is accepted as a provider-compatible fallback. The API key
is represented as a Pydantic `SecretStr`, never written to prompts, results,
or MCP audit events, and never included in provider error messages. The client
refuses to send credentials to any host other than the official HTTPS AI-Lab
host.

Do not commit `.env` or `litellm_user_info.txt`. If a key is exposed, follow
the provider instructions and request invalidation/regeneration through ITSC
Scientific Computing support.

## Running the agent

With VPN access and the environment configured:

```bash
python -m agent.ai_lab_agent
```

The default example requests only a dry-run plan.

## Explicit networked evaluation

Local tests never contact AI-Lab. The small live evaluation requires an
explicit acknowledgement because it consumes provider budget units:

```bash
python -m evaluation.evaluate_ai_lab_agent --confirm-network
```

It performs two generic dry-run cases. It does not execute a substrate.
Outputs are labeled `ai_lab_llm_inference_dry_run` and
`pnn_evidence=false`. They record model and budget provenance, sanitized plans
and summaries, audit verification, and the statement that project-provided
access incurs no recorded direct monetary charge.

## Reproducibility limitations

The provider may change model weights, serving configuration, model catalog,
and budget policy without repository changes. Sampling is set low but hosted
inference is not guaranteed to be bit-reproducible. Reproduction requires
University network/VPN access, a valid personal project key, available budget,
and the recorded model identifier.
