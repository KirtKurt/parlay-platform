import path from 'node:path';

function normalizeScope(value) {
  if (typeof value !== 'string' || value.includes('\\')) throw new Error(`invalid allowed scope: ${value}`);
  const raw = value.trim().replace(/^\.\//, '').replace(/\/$/, '');
  if (!raw || raw === '.' || raw.startsWith('/') || path.isAbsolute(raw)) throw new Error(`invalid allowed scope: ${value}`);
  const segments = raw.split('/');
  if (segments.some((segment) => !segment || segment === '.' || segment === '..' || segment === '.git')) throw new Error(`invalid allowed scope: ${value}`);
  return raw;
}

export function loadConfig(env = process.env) {
  const required = [
    'INQSI_ENGINEERING_OIDC_ISSUER',
    'INQSI_ENGINEERING_OIDC_AUDIENCE',
    'INQSI_ENGINEERING_JWKS_URI',
    'INQSI_ENGINEERING_ADMIN_CLAIM',
    'INQSI_ENGINEERING_REPOSITORY',
    'INQSI_ENGINEERING_DATA_DIR',
    'INQSI_ENGINEERING_WORKSPACE_ROOT',
    'INQSI_ENGINEERING_ORIGIN',
    'INQSI_ENGINEERING_ALLOWED_SCOPES'
  ];
  const missing = required.filter((name) => !String(env[name] || '').trim());
  if (missing.length) throw new Error(`Engineering console disabled: missing ${missing.join(', ')}`);

  for (const name of ['INQSI_ENGINEERING_REPOSITORY', 'INQSI_ENGINEERING_DATA_DIR', 'INQSI_ENGINEERING_WORKSPACE_ROOT']) {
    if (!path.isAbsolute(env[name])) throw new Error(`Engineering console disabled: ${name} must be absolute`);
  }

  let jwksUri;
  let allowedOrigin;
  try {
    jwksUri = new URL(env.INQSI_ENGINEERING_JWKS_URI).toString();
    allowedOrigin = new URL(env.INQSI_ENGINEERING_ORIGIN).origin;
  } catch {
    throw new Error('Engineering console disabled: invalid JWKS URI or origin');
  }
  if (!jwksUri.startsWith('https://') || !allowedOrigin.startsWith('https://')) {
    throw new Error('Engineering console disabled: JWKS URI and origin must use https');
  }

  const allowedScopes = [...new Set(env.INQSI_ENGINEERING_ALLOWED_SCOPES.split(',').map(normalizeScope))];
  if (!allowedScopes.length) throw new Error('Engineering console disabled: no allowed scopes configured');

  const maxConcurrentJobs = Number(env.INQSI_ENGINEERING_MAX_CONCURRENT_JOBS || 1);
  if (!Number.isInteger(maxConcurrentJobs) || maxConcurrentJobs < 1 || maxConcurrentJobs > 8) {
    throw new Error('Engineering console disabled: invalid max concurrent jobs');
  }

  return {
    issuer: env.INQSI_ENGINEERING_OIDC_ISSUER,
    audience: env.INQSI_ENGINEERING_OIDC_AUDIENCE,
    jwksUri,
    adminClaim: env.INQSI_ENGINEERING_ADMIN_CLAIM,
    repository: path.resolve(env.INQSI_ENGINEERING_REPOSITORY),
    dataDir: path.resolve(env.INQSI_ENGINEERING_DATA_DIR),
    workspaceRoot: path.resolve(env.INQSI_ENGINEERING_WORKSPACE_ROOT),
    allowedOrigin,
    allowedScopes,
    requiredChecks: [...new Set(String(env.INQSI_ENGINEERING_REQUIRED_CHECKS || 'build').split(',').map((item) => item.trim()).filter(Boolean))],
    maxConcurrentJobs,
    port: Number(env.PORT || 8787),
    maxInstructionBytes: Number(env.INQSI_ENGINEERING_MAX_INSTRUCTION_BYTES || 20000)
  };
}
