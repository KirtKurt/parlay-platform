# InQsi Deploy and AWS-Side AI Verification Policy

This is the operating rule for all InQsi code changes made through ChatGPT/GitHub.

## Required sequence

Every code change must follow this sequence:

1. Commit the code to GitHub.
2. Let the relevant GitHub Actions deploy workflow run.
3. Verify the AWS-side deployment result.
4. Run an AWS-side OpenAI bridge test when the change touches backend, Lambda, AI tooling, deployment workflows, sports API processing, or admin AI functionality.
5. If a deploy or test fails, use the InQsi AI Tools workflow with `deployment_diagnosis` or `failed_log_summary` before making the next code change.

## What counts as AWS-side testing

Preferred path:

`GitHub Actions -> AWS Lambda direct invoke -> AWS Secrets Manager -> OpenAI -> result`

This avoids API Gateway timeout risk and confirms the real AWS Lambda/OpenAI bridge path is working.

## Model policy

- Default public/API Gateway path: `gpt-5-mini`, medium reasoning.
- Internal deploy/code/log tools: `gpt-5-mini`, medium reasoning.
- Sports API algorithm research: `gpt-5-pro`, high reasoning, direct Lambda only.

## Safety rules

- Do not expose API keys, admin tokens, AWS keys, or GitHub secrets in logs or frontend code.
- Do not invent data.
- Do not use default zeros.
- Do not pretend a deployment or test passed unless GitHub/AWS confirms it.
- Report failures clearly and stop until corrected.

## Owner expectation

This policy exists because the OpenAI integration is intended to be used continuously as part of the InQsi build/deploy/test loop, not only as a one-time setup.
