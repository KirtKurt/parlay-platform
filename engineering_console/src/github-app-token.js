import { createPrivateKey } from 'node:crypto';
import { SignJWT } from 'jose';
import { aws } from './aws-cli.js';

export function createInstallationTokenProvider({ secretArn = process.env.INQSI_ENGINEERING_GITHUB_APP_SECRET_ARN, load = aws, request = fetch, now = () => Date.now() } = {}) {
  if (!/^arn:aws[a-z-]*:secretsmanager:[^:]+:\d{12}:secret:.+$/.test(secretArn || '')) throw new Error('publisher_app_secret_reference_required');
  let cached;
  return async () => {
    if (cached && cached.expires > now() + 300000) return cached.token;
    try {
      const result = await load('secretsmanager', 'get-secret-value', { SecretId: secretArn });
      const { appId, installationId, privateKey } = JSON.parse(result.SecretString);
      if (!/^\d+$/.test(String(appId)) || !/^\d+$/.test(String(installationId))) throw new Error();
      const issued = Math.floor(now() / 1000);
      const jwt = await new SignJWT({}).setProtectedHeader({ alg: 'RS256' }).setIssuer(String(appId)).setIssuedAt(issued - 30).setExpirationTime(issued + 540).sign(createPrivateKey(privateKey));
      const response = await request(`https://api.github.com/app/installations/${installationId}/access_tokens`, {
        method: 'POST', redirect: 'error', signal: AbortSignal.timeout(30000),
        headers: { authorization: `Bearer ${jwt}`, accept: 'application/vnd.github+json', 'x-github-api-version': '2022-11-28', 'content-type': 'application/json' },
        body: JSON.stringify({ repositories: ['parlay-platform'], permissions: { contents: 'write', pull_requests: 'write', checks: 'read', actions: 'read' } })
      });
      if (!response.ok) throw new Error();
      const data = await response.json();
      const expires = Date.parse(data.expires_at);
      if (!/^ghs_[A-Za-z0-9_]+$/.test(data.token || '') || !Number.isFinite(expires) || expires <= now() + 300000 || expires > now() + 3900000) throw new Error();
      cached = { token: data.token, expires };
      return cached.token;
    } catch { cached = undefined; throw new Error('publisher_installation_token_unavailable'); }
  };
}
