import crypto from 'node:crypto';
import { SignJWT, jwtVerify, createRemoteJWKSet } from 'jose';

export const ACCESS_COOKIE = '__Host-inqsi_engineering_access';
const FLOW_COOKIE = '__Host-eng_console_login';
export function cookieValue(header, name) {
  const values = String(header || '').split(';').map(x => x.trim()).filter(x => x.startsWith(`${name}=`));
  return values.length === 1 ? values[0].slice(name.length + 1) : null;
}
function cookie(name, value, seconds, sameSite = 'Strict') { return `${name}=${value}; HttpOnly; Secure; SameSite=${sameSite}; Path=/; Max-Age=${seconds}`; }
export function createBrowserAuth(config, authorize, request = fetch, identityVerifier = null) {
  if (!config.oidcClientId || !config.oidcClientSecret || Buffer.byteLength(config.sessionKey || '') < 32) throw new Error('browser_oidc_configuration_required');
  const key = new TextEncoder().encode(config.sessionKey);
  const jwks = createRemoteJWKSet(new URL(config.jwksUri));
  let discovery;
  async function discover() {
    if (discovery) return discovery;
    const result = await request(`${config.issuer.replace(/\/$/, '')}/.well-known/openid-configuration`, { redirect: 'error', signal: AbortSignal.timeout(10000) });
    if (!result.ok) throw new Error('oidc_discovery_failed');
    const data = await result.json();
    if (data.issuer !== config.issuer) throw new Error('oidc_issuer_mismatch');
    for (const field of ['authorization_endpoint', 'token_endpoint']) { const endpoint = new URL(data[field]); if (endpoint.protocol !== 'https:' || endpoint.username || endpoint.password || endpoint.hash) throw new Error('oidc_endpoint_invalid'); }
    discovery = data; return data;
  }
  return async (req, res) => {
    const url = new URL(req.url, config.allowedOrigin);
    if (!url.pathname.startsWith('/auth/')) return false;
    const redirect = (location, cookies = []) => { res.writeHead(302, { location, 'set-cookie': cookies, 'cache-control': 'no-store' }); res.end(); return true; };
    try {
      if (req.method === 'GET' && url.pathname === '/auth/login') {
        const data = await discover();
        const state = crypto.randomBytes(24).toString('base64url');
        const nonce = crypto.randomBytes(24).toString('base64url');
        const verifier = crypto.randomBytes(48).toString('base64url');
        const flow = await new SignJWT({ state, nonce, verifier }).setProtectedHeader({ alg: 'HS256' }).setIssuedAt().setExpirationTime('5m').sign(key);
        const target = new URL(data.authorization_endpoint);
        target.search = new URLSearchParams({ response_type: 'code', client_id: config.oidcClientId, redirect_uri: `${config.allowedOrigin}/auth/callback`, scope: 'openid profile', state, nonce, code_challenge_method: 'S256', code_challenge: crypto.createHash('sha256').update(verifier).digest('base64url') }).toString();
        return redirect(target.toString(), [cookie(FLOW_COOKIE, flow, 300, 'Lax')]);
      }
      if (req.method === 'GET' && url.pathname === '/auth/callback') {
        const { payload: flow } = await jwtVerify(cookieValue(req.headers.cookie, FLOW_COOKIE) || '', key, { algorithms: ['HS256'] });
        if (!url.searchParams.get('code') || url.searchParams.get('state') !== flow.state) throw new Error('oidc_state_mismatch');
        const data = await discover();
        const result = await request(data.token_endpoint, { method: 'POST', redirect: 'error', signal: AbortSignal.timeout(15000), headers: { 'content-type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ grant_type: 'authorization_code', code: url.searchParams.get('code'), code_verifier: flow.verifier, client_id: config.oidcClientId, client_secret: config.oidcClientSecret, redirect_uri: `${config.allowedOrigin}/auth/callback` }) });
        if (!result.ok) throw new Error('oidc_exchange_failed');
        const tokens = await result.json();
        const { payload: identity } = await (identityVerifier || jwtVerify)(tokens.id_token, jwks, { issuer: config.issuer, audience: config.oidcClientId });
        if (identity.nonce !== flow.nonce) throw new Error('oidc_nonce_mismatch');
        const actor = await authorize({ method: 'GET', headers: { authorization: `Bearer ${tokens.access_token}` } });
        if (actor.id !== identity.sub) throw new Error('oidc_subject_mismatch');
        return redirect('/', [cookie(FLOW_COOKIE, '', 0, 'Lax'), cookie(ACCESS_COOKIE, tokens.access_token, Math.min(Number(tokens.expires_in) || 300, 3600))]);
      }
      if (req.method === 'POST' && url.pathname === '/auth/logout' && req.headers.origin === config.allowedOrigin) return redirect('/auth/login', [cookie(ACCESS_COOKIE, '', 0)]);
      res.writeHead(400, { 'cache-control': 'no-store' }); res.end('Invalid authentication request'); return true;
    } catch {
      res.writeHead(401, { 'content-type': 'text/plain', 'cache-control': 'no-store', 'set-cookie': cookie(FLOW_COOKIE, '', 0, 'Lax') });
      res.end('Sign-in failed. Check the configured issuer, client, audience and administrator claim.'); return true;
    }
  };
}
