import path from 'node:path';

export function loadConfig(env = process.env) {
  const required = ['INQSI_ENGINEERING_OIDC_ISSUER', 'INQSI_ENGINEERING_OIDC_AUDIENCE', 'INQSI_ENGINEERING_ADMIN_CLAIM', 'INQSI_ENGINEERING_REPOSITORY'];
  const missing = required.filter((name) => !env[name]);
  if (missing.length) throw new Error(`Engineering console disabled: missing ${missing.join(', ')}`);
  const repository = path.resolve(env.INQSI_ENGINEERING_REPOSITORY);
  return {
    issuer: env.INQSI_ENGINEERING_OIDC_ISSUER,
    audience: env.INQSI_ENGINEERING_OIDC_AUDIENCE,
    adminClaim: env.INQSI_ENGINEERING_ADMIN_CLAIM,
    repository,
    dataDir: path.resolve(env.INQSI_ENGINEERING_DATA_DIR || '.inqsi-engineering'),
    workspaceRoot: path.resolve(env.INQSI_ENGINEERING_WORKSPACE_ROOT || path.join(repository, '..', 'inqsi-workspaces')),
    port: Number(env.PORT || 8787),
    allowedOrigin: env.INQSI_ENGINEERING_ORIGIN || '',
    maxInstructionBytes: Number(env.INQSI_ENGINEERING_MAX_INSTRUCTION_BYTES || 20000)
  };
}
