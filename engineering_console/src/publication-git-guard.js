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
function expectedFiles(manifest) { return [...manifest.changedFiles].sort(); }

export async function assertMainAncestor(repo, startingRevision, mainRevision) {
  sha(startingRevision); sha(mainRevision);
  try { await git(repo, ['merge-base', '--is-ancestor', startingRevision, mainRevision]); }
  catch { throw new Error('publication_start_not_on_main'); }
}

async function validateFiles(repo, manifest, base, head, policy) {
  const files = (await git(repo, ['diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--name-only', '-z', base, head, '--'])).split('\0').filter(Boolean).sort();
  if (!files.length || files.length > policy.maxFiles || JSON.stringify(files) !== JSON.stringify(expectedFiles(manifest))) throw new Error('publication_complete_diff_mismatch');
  if (files.some((file) => !isProofFile(file) || !withinAuthorizedScope(file, policy.allowedScopes) || !withinAuthorizedScope(file, manifest.authorizedScope))) throw new Error('publication_complete_diff_scope_violation');
  const patch = await git(repo, ['diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--binary', base, head, '--']);
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
  return files;
}

export async function validatePublicationHistory(repo, manifest, head, main, policy, { alreadyMerged = false } = {}) {
  sha(head); sha(main); sha(manifest.startingRevision);
  await assertMainAncestor(repo, manifest.startingRevision, main);
  const parents = (await git(repo, ['show', '-s', '--format=%P', head])).trim().split(' ').filter(Boolean);
  if (parents.length !== 1 || parents[0] !== manifest.startingRevision) throw new Error('publication_unexpected_parent');

  // Before merge, bind the candidate to the exact trusted-main merge base. After
  // an external merge, the reviewed head may itself be an ancestor of main (a
  // normal merge) or may not be present in main at all (squash/rebase). In that
  // recovery case, verify the immutable reviewed head directly against its
  // recorded trusted parent; validateMergedPublication separately verifies the
  // actual commit that landed on main and exact resulting blob contents.
  const base = alreadyMerged ? manifest.startingRevision : (await git(repo, ['merge-base', main, head])).trim();
  if (!alreadyMerged && base !== manifest.startingRevision) throw new Error('publication_unexpected_merge_base');
  const files = await validateFiles(repo, manifest, base, head, policy);
  return { files, head, main, startingRevision: manifest.startingRevision };
}

/** Validate a GitHub merge/squash/rebase result without assuming the original
 * PR head itself became an ancestor of main. The merged commit must be on main,
 * must introduce exactly the authorized proof files relative to its first
 * parent, and every resulting proof blob must exactly match the reviewed PR
 * head. A normal merge additionally binds its second parent to the PR head. */
export async function validateMergedPublication(repo, manifest, publishedHead, mergeCommit, main, policy) {
  sha(publishedHead); sha(mergeCommit); sha(main); sha(manifest.startingRevision);
  await validatePublicationHistory(repo, manifest, publishedHead, main, policy, { alreadyMerged: true });
  try { await git(repo, ['merge-base', '--is-ancestor', mergeCommit, main]); }
  catch { throw new Error('publication_merge_not_on_main'); }
  const parents = (await git(repo, ['show', '-s', '--format=%P', mergeCommit])).trim().split(' ').filter(Boolean);
  if (parents.length < 1 || parents.length > 2) throw new Error('publication_merge_parent_shape_invalid');
  if (parents.length === 2 && parents[1] !== publishedHead) throw new Error('publication_merge_head_mismatch');
  const files = await validateFiles(repo, manifest, parents[0], mergeCommit, policy);
  for (const file of files) {
    const expected = (await git(repo, ['rev-parse', `${publishedHead}:${file}`]).catch(() => '')).trim();
    const merged = (await git(repo, ['rev-parse', `${mergeCommit}:${file}`]).catch(() => '')).trim();
    if (expected !== merged) throw new Error('publication_merged_content_mismatch');
  }
  return { files, publishedHead, mergeCommit, main };
}
