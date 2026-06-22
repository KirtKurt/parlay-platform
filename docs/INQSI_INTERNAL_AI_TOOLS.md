# InQsi Internal AI Tools

This is the owner-only AI layer for InQsi engineering, deployment support, admin tooling, and sports algorithm research.

## Active tools

1. `code_review`
   - Reviews InQsi code, diffs, architecture, security risk, and missing tests.
   - Default: `gpt-5-mini`, medium reasoning.

2. `deployment_diagnosis`
   - Diagnoses GitHub Actions, SAM, CloudFormation, Lambda, and AWS deployment errors.
   - Default: `gpt-5-mini`, medium reasoning.

3. `failed_log_summary`
   - Summarizes failed logs into cause, impact, and next action.
   - Default: `gpt-5-mini`, medium reasoning.

4. `admin_tool_plan`
   - Designs internal owner-only AI tools without exposing secrets in browser code.
   - Default: `gpt-5-mini`, medium reasoning.

5. `sports_api_algorithm_lab`
   - Uses `gpt-5-pro`, high reasoning.
   - Purpose: analyze real sports API/market samples and recommend stronger, testable algorithm improvements across sports.
   - Rules: no fake data, no default zeros, no guaranteed-win claims, no odds-only build recommendations.

## How to run

Use GitHub Actions:

`Actions -> InQsi AI Tools -> Run workflow`

Choose the tool, paste the prompt, optionally paste logs/API sample/context, then run.

## Safety rules

- OpenAI API key stays in AWS Secrets Manager.
- The internal tools call the Lambda through AWS IAM for owner-only workflows.
- Public/member-facing API Gateway routes default to `gpt-5-mini` with medium reasoning to reduce timeouts.
- GPT-5 Pro high reasoning is reserved for direct internal jobs such as sports API algorithm research.
- Do not paste secrets, access keys, tokens, or private customer/member data into prompts.

## Connection to app

The internal admin page is available at:

`/admin/ai`

It lists the available tools and directs the owner to the GitHub Actions workflow. This avoids exposing OpenAI credentials or admin tokens in the browser.
