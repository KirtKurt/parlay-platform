import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { JOB_ID } from './publication.js';

export const MAX_TRANSPORT_BYTES = 64 * 1024 * 1024;
export function transportDirectory(root, id) {
  if (!path.isAbsolute(root) || !JOB_ID.test(id || '')) throw new Error('invalid_task_transport');
  return path.join(root, id);
}
export function atomicTransportWrite(target, bytes, { immutable = false } = {}) {
  const temporary = `${target}.${crypto.randomUUID()}.tmp`;
  try {
    const fd = fs.openSync(temporary, 'wx', 0o600);
    try { fs.writeFileSync(fd, bytes); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
    if (immutable) fs.linkSync(temporary, target);
    else fs.renameSync(temporary, target);
    const directory = fs.openSync(path.dirname(target), 'r');
    try { fs.fsyncSync(directory); } finally { fs.closeSync(directory); }
  } finally { fs.rmSync(temporary, { force: true }); }
}
export function createTaskTransport(root, input, previousCheckpoint = null) {
  const id = crypto.randomUUID();
  const directory = transportDirectory(root, id);
  fs.mkdirSync(directory, { recursive: false, mode: 0o700 });
  const token = `ecj_${id}_${crypto.randomBytes(32).toString('hex')}`;
  atomicTransportWrite(path.join(directory, 'auth.json'), JSON.stringify({ token, hash: crypto.createHash('sha256').update(token).digest('hex'), expires: Date.now() + 35 * 60 * 1000, maxRequests: 200 }));
  const payload = Buffer.from(JSON.stringify({ ...input, checkpoint: previousCheckpoint }));
  if (payload.length > MAX_TRANSPORT_BYTES) throw new Error('task_input_too_large');
  atomicTransportWrite(path.join(directory, 'input.json'), payload, { immutable: true });
  return { id, token, directory };
}
export function authenticateTask(root, authorization, now = Date.now()) {
  const match = /^Bearer (ecj_([0-9a-f-]{36})_[0-9a-f]{64})$/.exec(authorization || '');
  if (!match) throw new Error('task_authentication_required');
  const directory = transportDirectory(root, match[2]);
  const auth = JSON.parse(fs.readFileSync(path.join(directory, 'auth.json'), 'utf8'));
  const actual = crypto.createHash('sha256').update(match[1]).digest();
  const expected = Buffer.from(auth.hash || '', 'hex');
  if (expected.length !== actual.length || !crypto.timingSafeEqual(expected, actual) || !Number.isFinite(auth.expires) || auth.expires <= now) throw new Error('task_token_expired_or_invalid');
  return { id: match[2], directory, auth };
}
