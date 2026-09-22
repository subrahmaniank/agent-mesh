# Cedar policy set

One file per policy, because cedar-agent's `PUT /v1/policies` takes a JSON array
of `{id, content}` where **each `content` must hold exactly one Cedar
statement**. Submitting the whole set as a single entry fails with:

```
{"reason":"You have malformed a bad request",
 "description":"The content in the request does not match the specifications:
                unexpected token `forbid`", "code":400}
```

The filename (without `.cedar`) becomes the policy id in the PDP, so a denial
can be traced back to the file that caused it. `scripts/load-cedar.sh` loads
every `*.cedar` here in lexical order — hence the numeric prefixes.

    // AgentMesh IAM — evaluated by cedar-agent, enforced by agentgateway.
    //
    // Cedar is deny-by-default: an agent gets nothing unless a `permit` matches,
    // and any `forbid` overrides every `permit`. That property is what makes an
    // unregistered agent inert rather than merely unlisted.
    //
    // Principals are agents as published and approved in agentregistry; resources
    // are the models and MCP tools configured in agentgateway.
