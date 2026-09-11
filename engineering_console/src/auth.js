import { createRemoteJWKSet, jwtVerify } from 'jose';

export function createAuthorizer(config, verify = null) {
  const jwks = createRemoteJWKSet(new URL(config.jwksUri));
  const verifyToken = verify || ((token) => jwtVerify(token, jwks, {
    issuer: config.issuer,
    audience: config.audience
  }));

  return async function authorize(request) {
    const value = request.headers.authorization || '';
    if (!value.startsWith('Bearer ')) throw Object.assign(new Error('authentication_required'), { status: 401 });

    let payload;
    try {
      ({ payload } = await verifyToken(value.slice(7)));
    } catch {
      throw Object.assign(new Error('invalid_access_token'), { status: 401 });
    }

    const administrator = payload[config.adminClaim];
    if (!(administrator === true || administrator === 'true' || (Array.isArray(administrator) && administrator.includes('admin')))) {
      throw Object.assign(new Error('administrator_required'), { status: 403 });
    }
    if (!payload.sub) throw Object.assign(new Error('subject_required'), { status: 401 });

    return { id: String(payload.sub) };
  };
}
