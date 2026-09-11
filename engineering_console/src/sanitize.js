const SECRET = /(api[_-]?key|authorization|token|secret|password|client[_-]?secret|access[_-]?key)\s*[:=]\s*[^\s,;]+/gi;
const BEARER = /(bearer\s+)[A-Za-z0-9._~+\/-]{10,}/gi;
const OPENAI_KEY = /sk-[A-Za-z0-9_-]{12,}/g;

export function redact(value) {
  return String(value ?? '')
    .replace(SECRET, '$1=[REDACTED]')
    .replace(BEARER, '$1[REDACTED]')
    .replace(OPENAI_KEY, '[REDACTED_OPENAI_KEY]');
}

export function sanitize(value) {
  return redact(value).slice(0, 12000);
}

export function sanitizeValue(value) {
  if (typeof value === 'string') return redact(value);
  if (Array.isArray(value)) return value.map(sanitizeValue);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, sanitizeValue(child)]));
  return value;
}

function sanitizePublicValue(value) {
  if (typeof value === 'string') return sanitize(value);
  if (Array.isArray(value)) return value.map(sanitizePublicValue);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, sanitizePublicValue(child)]));
  return value;
}

export function publicJob(job) {
  return sanitizePublicValue({ ...job, logs: job.logs || [], testResults: job.testResults || [], diff: job.diff || '', error: job.error || null });
}
