# Security, IAM & Data Governance

The platform employs a **Zero Ambient Authority** model: agents possess no static credentials. All authorizations, credential injections, and sanitizations occur in-flight at the **Agent Gateway**.

---

## 1. Cedar Policy Engine (PDP/PEP)

The gateway leverages AWS Cedar Policy Engine for sub-millisecond, deterministic authorization evaluations.

### 1.1 Schema (`gateway/policies/schema.cedarschema`)
Defines the entities (`User`, `UserGroup`, `Agent`, `Tool`) and actions (`invoke`, `call_tool`):

```cedar
namespace AgentPlatform {
  entity User in [UserGroup] {
    department: String,
    clearance: Long
  };
  entity UserGroup;

  entity Agent {
    owner: String,
    tier: String,
    model: String
  };

  entity Tool {
    mcp_server: String,
    risk_level: String,
    risk_level_score: Long,
    target_environment: String
  };

  action "invoke" appliesTo {
    principal: [User, Agent],
    resource: [Agent]
  };

  action "call_tool" appliesTo {
    principal: [Agent],
    resource: [Tool],
    context: {
      user_id: String,
      user_clearance: Long,
      hitl_approved: Boolean,
      payload: String
    }
  };
}
```

### 1.2 Base Guardrails (`gateway/policies/base_guardrails.cedar`)
Implements pre-flight authorization rules, dynamic clearance checks, and hard security invariants:

```cedar
// Permit authorized operators to invoke agents
permit (
    principal in AgentPlatform::UserGroup::"AuthorizedOperators",
    action == AgentPlatform::Action::"invoke",
    resource in AgentPlatform::Agent::"default-agent"
);

// Dynamic Tool Scoping: clearance must meet or exceed resource risk score
permit (
    principal,
    action == AgentPlatform::Action::"call_tool",
    resource
)
when {
    context.user_clearance >= resource.risk_level_score
};

// OWASP LLM06 / Destructive Invariant: Forbid destructive actions without confirmed HITL
forbid (
    principal,
    action == AgentPlatform::Action::"call_tool",
    resource
)
when {
    (resource.risk_level == "destructive" ||
     context.payload.contains("DROP TABLE") ||
     context.payload.contains("DELETE FROM") ||
     context.payload.contains("rm -rf")) &&
    context.hitl_approved != true
};
```

---

## 2. In-Flight DLP & Privacy (Microsoft Presidio)

The platform prevents data leakage (SSNs, phone numbers, credit cards, emails) before payloads reach model providers.

### 2.1 Presidio Integration
- **Presidio Analyzer**: Detects PII entities using regular expressions and named entity recognition models.
- **Presidio Anonymizer**: Replaces sensitive spans with structured surrogate tokens (e.g., `<EMAIL_ADDRESS_1>`, `<PHONE_NUMBER_1>`).
- Configured in `gateway/config.yaml` and customizable via `presidio/conf/recognizers.yaml`.

---

## 3. Zero Data Retention (ZDR)

The telemetry pipeline strips sensitive input/output text while maintaining observability metadata:

- **Deleted Keys**: `gen_ai.prompt`, `gen_ai.completion`, `llm.prompts`, `llm.completions`, `input.value`, `output.value`.
- **Retained Metadata**: Latency, token metrics, model identifiers, tenant IDs, and `privacy.sanitized=true`.
