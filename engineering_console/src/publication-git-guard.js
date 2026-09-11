import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { isProofFile } from './publication-policy.js';
import { patchContainsCredential, withinAuthorizedScope } from './publication.js';

const exec = promisify(execFile);
const SHA = /^[0-9a-f]{40}$/;
async function git(repo, args) {
  const { stdout } = await exec('git', ['-c', 'core.hooksPath=/dev/null', ...args], { cwd: repo, maxBuffer: 2 * 1024 * 1024, timeout: 30000 });
  return stdout;
}
function sha(value) { if (!SHA.test(value || '')) throw new Error('invalid_publication_revision'); }

export async function assertMainAncestor(repo, startingRevision, mainRevision) {
  sha(startingRevision); sha(mainRevision);
  try { await git(repo, ['merge-base', '--is-ancestor', startingRevision, mainRevision]); }
  catch { throw new Error('publication_start_not_on_main'); }
}

export async function validatePublicationHistory(repo, manifest, head, main, policy, { alreadyMerged = false } = {}) {
  sha(head); sha(main); sha(manifest.startingRevision);
  await assertMainAncestor(repo, manifest.startingRevision, main);
  const parents = (await git(repo, ['show', '-s', '--format=%P', head])).trim().split(' ');
  if (parents.length !== 1 || parents[0] !== manifest.startingRevision) throw new Error('publication_unexpected_parent');
  const base = (await git(repo, ['merge-base', main, head])).trim();
  if (alreadyMerged) {
    if (base !== head) throw new Error('publication_merge_not_on_main');
  } else if (base !== manifest.startingRevision) {
    throw new Error('publication_unexpected_merge_base');
  }
  // For an open PR, this is the complete main...head diff, not a worker-local diff.
  // On recovery of an externally merged PR, the single-parent constraint plus
  // the original start..head diff still binds the exact original patch content.
  const diffBase = alreadyMerged ? manifest.startingRevision : base;
  const files = (await git(repo, ['diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--name-only', '-z', diffBase, head, '--'])).split('\0').filter(Boolean).sort();
  if (!files.length || files.length > policy.maxFiles || JSON.stringify(files) !== JSON.stringify([...manifest.changedFiles].sort())) throw new Error('publication_complete_diff_mismatch');
  if (files.some((file) => !isProofFile(file) || !withinAuthorizedScope(file, policy.allowedScopes) || !withinAuthorizedScope(file, manifest.authorizedScope))) throw new Error('publication_complete_diff_scope_violation');
  const patch = await git(repo, ['diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--binary', diffBase, head, '--']);
  if (Buffer.byteLength(patch) > policy.maxPatchBytes || patchContainsCredential(patch)) throw new Error('publication_complete_diff_rejected');
  for (const file of files) {
    const entry = (await git(repo, ['ls-tree', head, '--', file])).trim();
    if (!entry) continue;
    if (!entry.startsWith('100644 blob ')) throw new Error('publication_file_mode_rejected');
    const object = entry.slice('100644 blob '.length).split('\t')[0];
    const size = Number((await git(repo, ['cat-file', '-s', object])).trim());
    if (!Number.isSafeInteger(size) || size > policy.maxFileBytes) throw new Error('publication_file_size_rejected');
    const contents = await git(repo, ['cat-file', 'blob', object]);
    if (contents.includes('\0') || patchContainsCredential(contents)) throw new Error('publication_file_content_rejected');
  }
  return { files, head, main, startingRevision: manifest.startingRevision };
}
