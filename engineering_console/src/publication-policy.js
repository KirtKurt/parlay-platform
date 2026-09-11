import path from 'node:path';
import { normalizeRepoPath, validatePublicationManifest, withinAuthorizedScope } from './publication.js';

export const PROOF_ROOT = 'engineering_console_publication_proof';
export const PROOF_CHECK = 'engineering-console-publication-proof';
export const PROOF_WORKFLOW = '.github/workflows/engineering-console-publication-proof.yml';
export const REPOSITORY = 'KirtKurt/parlay-platform';

export function isPublishableScope(value) {
  const normalized = normalizeRepoPath(value);
  if (normalized !== value) return false;
  return value === PROOF_ROOT || new RegExp(`^${PROOF_ROOT}/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*(?:\\.md)?$`).test(value);
}

export function isProofFile(value) {
  return typeof value === 'string' && new RegExp(`^${PROOF_ROOT}/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\\.md$`).test(value);
}

export function loadPublicationPolicy(env = process.env) {
  if (env.INQSI_ENGINEERING_PUBLICATION_POLICY !== 'proof-v1') throw new Error('explicit_publication_policy_required');
  const scopes = String(env.INQSI_ENGINEERING_ALLOWED_SCOPES || '').split(',').map((v) => v.trim());
  if (!scopes.length || scopes.some((v) => !isPublishableScope(v))) throw new Error('publication_policy_scope_invalid');
  const checks = String(env.INQSI_ENGINEERING_REQUIRED_CHECKS || '').split(',').map((v) => v.trim());
  if (checks.length !== 1 || checks[0] !== PROOF_CHECK) throw new Error('publication_policy_checks_invalid');
  if (env.GITHUB_REPOSITORY && env.GITHUB_REPOSITORY !== REPOSITORY) throw new Error('publisher_repository_mismatch');
  return Object.freeze({ repository: REPOSITORY, allowedScopes: Object.freeze([...new Set(scopes)]), requiredChecks: Object.freeze(checks), maxFiles: 20, maxPatchBytes: 131072, maxFileBytes: 65536 });
}

export function validatePublisherRequest(manifest, patch, policy) {
  validatePublicationManifest(manifest, patch);
  if (manifest.repository !== policy.repository) throw new Error('publisher_repository_mismatch');
  if (manifest.authorizedScope.some((scope) => !isPublishableScope(scope) || !withinAuthorizedScope(scope, policy.allowedScopes))) throw new Error('publisher_scope_not_authorized');
  if (manifest.changedFiles.some((file) => !isProofFile(file) || !withinAuthorizedScope(file, policy.allowedScopes))) throw new Error('publisher_file_not_authorized');
  if (manifest.changedFiles.length > policy.maxFiles || new Set(manifest.changedFiles).size !== manifest.changedFiles.length) throw new Error('publisher_file_limit');
  if (Buffer.byteLength(patch) > policy.maxPatchBytes) throw new Error('publisher_patch_limit');
  if (JSON.stringify([...manifest.requiredChecks].sort()) !== JSON.stringify([...policy.requiredChecks].sort())) throw new Error('publisher_check_policy_mismatch');
  return true;
}

export function requirePublisherLockDirectory(env = process.env) {
  const directory = String(env.INQSI_ENGINEERING_PUBLISHER_LOCK_DIR || '');
  if (!path.isAbsolute(directory)) throw new Error('absolute_publisher_lock_dir_required');
  return directory;
}

export function validatePullRequestIdentity(pr, manifest, head) {
  if (!Number.isSafeInteger(pr?.number) || pr.number < 1 || pr.draft || pr.head?.repo?.full_name !== REPOSITORY || pr.base?.repo?.full_name !== REPOSITORY || pr.head?.ref !== manifest.branch || pr.base?.ref !== 'main' || pr.head?.sha !== head) throw new Error('publication_pr_identity_mismatch');
  if (!pr.merged_at && pr.state !== 'open') throw new Error('publication_pr_closed_without_merge');
}
