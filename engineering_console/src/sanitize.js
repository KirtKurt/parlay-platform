const SECRET = /(api[_-]?key|authorization|token|secret|password)\s*[:=]\s*[^\s,]+/gi;
const OPENAI_KEY = /sk-[A-Za-z0-9_-]{12,}/g;
export function sanitize(value) { return String(value).replace(SECRET, '$1=[REDACTED]').replace(OPENAI_KEY, '[REDACTED_OPENAI_KEY]').slice(0, 12000); }
export function publicJob(job) { const { instruction, ...safe } = job; return { ...safe, instruction, logs: job.logs.map(sanitize), error: job.error ? sanitize(job.error) : null }; }
