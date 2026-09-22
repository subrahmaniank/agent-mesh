# Framework integrations

Any framework works, because the integration point is an ordinary
OpenAI-compatible HTTP endpoint. There is no platform SDK to adopt.

---

## The general shape

Point the framework's LLM client at the gateway and give it the agent's bearer
token:

```
base_url = http://localhost:4000/v1
api_key  = $GATEWAY_TOKEN
```

That is the whole integration. Authentication, Cedar authorization, PII
guardrails, provider routing and token/cost telemetry all happen behind it.

## LangChain / LangGraph

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://localhost:4000/v1",
    api_key=os.environ["GATEWAY_TOKEN"],
    model="llama3",          # a model name from agentgateway/config.yaml
)
```

## CrewAI

```python
from crewai import Agent, LLM

llm = LLM(
    model="openai/llama3",   # CrewAI routes OpenAI-compatible via this prefix
    base_url="http://localhost:4000/v1",
    api_key=os.environ["GATEWAY_TOKEN"],
)
researcher = Agent(role="Researcher", goal="...", llm=llm)
```

## AutoGen

```python
config_list = [{
    "model": "llama3",
    "base_url": "http://localhost:4000/v1",
    "api_key": os.environ["GATEWAY_TOKEN"],
}]
```

## Anything else

Direct HTTP works identically — see
[`agent-onboarding-guide.md`](agent-onboarding-guide.md#calling-through-the-gateway).

---

## Switching providers

`model` names an entry in `agentgateway/config.yaml`, not a provider. Moving an
agent from a local model to a hosted one — or between clouds — is a change to
that file, or to which model name the agent asks for. The framework code above
does not change, and the governance travels with it: guardrails, Cedar
authorization and telemetry apply to every backend.

Whether an agent *may* use a given model is a Cedar decision, not a
configuration detail — an agent granted only the `local` tier is denied hosted
providers no matter what it passes as `model`.

## Step-level governance

Framework integration covers the wire. To govern the agent's internal steps,
add Agent Control's decorator to the functions you want checked — see
[`agent-onboarding-guide.md`](agent-onboarding-guide.md#adding-step-level-controls).
Agent Control ships integrations for LangChain, LangGraph, CrewAI, Google ADK
and AWS Strands.

## MCP tools

Tools reach agents through agentgateway's MCP gateway rather than being wired
into each framework separately, so the same authorization and guardrails apply.
Register the server in agentregistry, add it to `mcp.targets`, and give it a
Cedar `Tool` entity.
