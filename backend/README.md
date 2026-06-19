# Silvers Syndicate Backend

AWS SAM backend for the Silvers Syndicate API.

## API routes

```bash
GET /v1/health
GET /v1/slates/today
GET /v1/games/{game_id}/snapshots
GET /v1/games/{game_id}/line-movement
POST /v1/parlays/build
GET /v1/parlays/{build_id}
```

## Deploy

From the repository root:

```bash
cd backend
sam build
sam deploy --guided
```

Use these values when prompted:

```bash
Stack Name: silvers-syndicate-api
AWS Region: us-east-1
Confirm changes before deploy: Y
Allow SAM CLI IAM role creation: Y
Disable rollback: N
Save arguments to configuration file: Y
SAM configuration file: samconfig.toml
SAM configuration environment: default
```

After deployment, copy the output value named:

```bash
ApiUrl
```

Add it to Amplify environment variables:

```bash
NEXT_PUBLIC_API_BASE_URL=<ApiUrl>
```

Then redeploy the Amplify frontend.
