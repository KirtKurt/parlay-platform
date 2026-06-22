# InQsi Bedrock Engineering Agent Setup

This guide wires an Amazon Bedrock Agent to the existing InQsi GitHub -> AWS deploy bridge.

## What this adds

The Bedrock agent action layer does not replace GitHub Actions.

It gives a Bedrock agent safe tools to:

- inspect the AWS caller identity
- inspect the `parlay-platform-dev` CloudFormation stack
- list InQsi DynamoDB tables
- list InQsi Lambda functions
- find the backend health-check URL
- trigger the existing GitHub deploy workflow
- read recent GitHub deploy workflow runs

## Files added

- `backend/src/inqsi_bedrock_agent_actions.py`
- `bedrock/inqsi-agent-openapi.yaml`
- `bedrock-agent-template.yaml`
- `.github/workflows/deploy-bedrock-agent.yml`

## AWS stack deployed by GitHub

The GitHub workflow deploys a separate stack:

```text
inqsi-bedrock-agent-actions
```

This keeps the agent action layer separate from the main app stack:

```text
parlay-platform-dev
```

## After GitHub deploys

Open AWS CloudFormation and find:

```text
inqsi-bedrock-agent-actions
```

Go to **Outputs** and copy:

```text
InqsiBedrockAgentActionsFunctionArn
```

This is the Lambda ARN to use as the Bedrock action group executor.

## Create the Bedrock agent

In AWS Console:

1. Go to **Amazon Bedrock**.
2. Go to **Agents**.
3. Create an agent named:

```text
InQsi Engineering Agent
```

Suggested instruction:

```text
You are the InQsi engineering and operations agent. You inspect AWS deployment state, DynamoDB tables, Lambda functions, API health targets, and GitHub deployment runs. You do not directly mutate production AWS resources except by calling approved GitHub deployment workflows. You never invent deployment state. If a resource is missing or a provider is not configured, say so clearly.
```

## Add action group

Create an action group named:

```text
InqsiEngineeringActions
```

Action group executor:

```text
Use Lambda function
```

Paste the Lambda ARN from CloudFormation output:

```text
InqsiBedrockAgentActionsFunctionArn
```

Schema type:

```text
OpenAPI schema
```

Use the contents of:

```text
bedrock/inqsi-agent-openapi.yaml
```

## Optional GitHub deploy triggering

AWS inspection works without a GitHub token.

GitHub workflow triggering and reading private workflow runs requires a GitHub token stored in AWS Secrets Manager.

Recommended secret name:

```text
inqsi/github/deploy-token
```

Recommended secret JSON:

```json
{
  "GITHUB_TOKEN": "ghp_or_fine_grained_token_here"
}
```

Then update the stack parameter:

```text
GitHubTokenSecretName=inqsi/github/deploy-token
```

## Current safety posture

The Bedrock action Lambda is intentionally limited:

- read CloudFormation stack status
- read DynamoDB table status
- read Lambda function status
- read API health target
- trigger GitHub deploy workflow if a token is configured
- read GitHub workflow runs if a token is configured

It does not directly update production DynamoDB records, Lambda code, IAM roles, or CloudFormation stacks.

## Social posting default

Connected social accounts are intended to have social posting enabled by default after the member grants provider posting permission. The platform must still respect provider OAuth scopes and member revocation.
