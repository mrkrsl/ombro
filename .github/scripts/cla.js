// The contributor agreement check, run by .github/workflows/cla.yml through actions/github-script.
//
// Everyone who opened the pull request or wrote one of its commits must have accepted CLA.md,
// once, by commenting the sentence below. Acceptances are kept in signatures.json on the
// cla-signatures branch (a branch of its own, so main's history stays clean). The result is a
// commit status called "CLA" on the pull request's head, which the main branch's ruleset requires.
// The repository's owner and bots don't sign. The pull request's own code is never run here.

const PHRASE = 'I have read the Ombro Contributor Agreement and I agree to it';
const VERSION = '1.0';
const BRANCH = 'cla-signatures';
const FILE = 'signatures.json';
const MARKER = '<!-- ombro-cla -->';

const normalise = (text) =>
  (text || '').trim().replace(/[.!]+$/, '').replace(/\s+/g, ' ').toLowerCase();

const exempt = (user, owner) =>
  user.type === 'Bot' || user.login.endsWith('[bot]') || user.login.toLowerCase() === owner.toLowerCase();

async function loadSignatures(github, owner, repo) {
  try {
    const { data } = await github.rest.repos.getContent({ owner, repo, path: FILE, ref: BRANCH });
    return { sha: data.sha, store: JSON.parse(Buffer.from(data.content, 'base64').toString('utf8')) };
  } catch (error) {
    if (error.status !== 404) throw error;
    return { sha: null, store: { signatures: [] } };
  }
}

async function ensureBranch(github, owner, repo) {
  try {
    await github.rest.repos.getBranch({ owner, repo, branch: BRANCH });
    return;
  } catch (error) {
    if (error.status !== 404) throw error;
  }
  // An orphan branch holding only the signatures file.
  const body = JSON.stringify({ signatures: [] }, null, 2) + '\n';
  const { data: blob } = await github.rest.git.createBlob({ owner, repo, content: body, encoding: 'utf-8' });
  const { data: tree } = await github.rest.git.createTree({
    owner, repo, tree: [{ path: FILE, mode: '100644', type: 'blob', sha: blob.sha }],
  });
  const { data: commit } = await github.rest.git.createCommit({
    owner, repo, message: 'Contributor agreement signatures', tree: tree.sha, parents: [],
  });
  await github.rest.git.createRef({ owner, repo, ref: `refs/heads/${BRANCH}`, sha: commit.sha });
}

async function recordSignature(github, owner, repo, signature) {
  await ensureBranch(github, owner, repo);
  for (let attempt = 0; attempt < 4; attempt++) {
    const { sha, store } = await loadSignatures(github, owner, repo);
    if (store.signatures.some((s) => s.id === signature.id)) return store;
    store.signatures.push(signature);
    try {
      await github.rest.repos.createOrUpdateFileContents({
        owner, repo, branch: BRANCH, path: FILE, ...(sha ? { sha } : {}),
        message: `${signature.login} accepted the contributor agreement (#${signature.pull_request})`,
        content: Buffer.from(JSON.stringify(store, null, 2) + '\n').toString('base64'),
      });
      return store;
    } catch (error) {
      if (error.status !== 409 && error.status !== 422) throw error; // someone else wrote first
    }
  }
  throw new Error('Could not record the signature after several tries');
}

function message(doc, missing, unlinked) {
  const lines = [MARKER, 'Thank you for your pull request.', ''];
  if (missing.length) {
    lines.push(
      `Before we can merge it, ${missing.map((u) => '@' + u.login).join(', ')} need${missing.length === 1 ? 's' : ''} to accept the [Ombro Contributor Agreement](${doc}). ` +
        'You keep your copyright; the agreement gives Heliotrope Atelier Ltd a licence to use your contribution, including under other terms. ' +
        'To accept it, comment this sentence on this pull request:',
      '',
      '```',
      PHRASE,
      '```',
      '',
      'You only do this once, and it covers your later pull requests too.',
    );
  }
  if (unlinked.length) {
    if (missing.length) lines.push('');
    lines.push(
      `Some commits are by ${unlinked.map((n) => `"${n}"`).join(', ')}, whose email isn't linked to a GitHub account, so we can't tell who wrote them. ` +
        'Add that email to your GitHub account (or rewrite the commits with one that is), then comment `recheck`.',
    );
  }
  return lines.join('\n');
}

module.exports = async ({ github, context, core }) => {
  const { owner, repo } = context.repo;
  const number = context.payload.pull_request?.number ?? context.payload.issue?.number;
  const doc = `https://github.com/${owner}/${repo}/blob/${context.payload.repository.default_branch}/CLA.md`;

  const { data: pr } = await github.rest.pulls.get({ owner, repo, pull_number: number });
  if (pr.state !== 'open') {
    core.info('The pull request is closed; nothing to check.');
    return;
  }

  // Who must have signed: the pull request's author and its commits' authors.
  const required = new Map();
  const unlinked = new Set();
  const need = (user) => {
    if (user && !exempt(user, owner)) required.set(user.id, { id: user.id, login: user.login });
  };
  need(pr.user);
  const commits = await github.paginate(github.rest.pulls.listCommits, {
    owner, repo, pull_number: number, per_page: 100,
  });
  for (const commit of commits) {
    if (commit.author) need(commit.author);
    else unlinked.add(commit.commit.author?.name || commit.commit.author?.email || commit.sha.slice(0, 7));
  }

  let { store } = await loadSignatures(github, owner, repo);

  // A comment accepting the agreement, from someone this pull request needs.
  const comment = context.payload.comment;
  if (context.eventName === 'issue_comment' && comment && normalise(comment.body) === normalise(PHRASE)) {
    const signer = required.get(comment.user.id);
    if (signer && !store.signatures.some((s) => s.id === signer.id)) {
      store = await recordSignature(github, owner, repo, {
        login: comment.user.login,
        id: comment.user.id,
        version: VERSION,
        pull_request: number,
        comment_id: comment.id,
        comment_url: comment.html_url,
        comment: comment.body,
        signed_at: comment.created_at,
      });
      core.info(`Recorded ${comment.user.login}'s acceptance.`);
    }
  }

  const signed = new Set(store.signatures.map((s) => s.id));
  const missing = [...required.values()].filter((u) => !signed.has(u.id));
  const ok = missing.length === 0 && unlinked.size === 0;

  await github.rest.repos.createCommitStatus({
    owner, repo, sha: pr.head.sha, context: 'CLA', target_url: doc,
    state: ok ? 'success' : 'failure',
    description: ok
      ? 'Everyone has accepted the contributor agreement'
      : missing.length
        ? `Waiting for ${missing.map((u) => u.login).join(', ')} to accept the contributor agreement`.slice(0, 140)
        : 'Some commits have an email not linked to a GitHub account',
  });

  // One comment of ours per pull request, kept up to date; none at all when nobody needed asking.
  const comments = await github.paginate(github.rest.issues.listComments, {
    owner, repo, issue_number: number, per_page: 100,
  });
  const ours = comments.find((c) => c.user.type === 'Bot' && c.body.includes(MARKER));
  const body = ok
    ? `${MARKER}\nThank you: everyone on this pull request has accepted the [Ombro Contributor Agreement](${doc}).`
    : message(doc, missing, [...unlinked]);
  if (ours && ours.body !== body) {
    await github.rest.issues.updateComment({ owner, repo, comment_id: ours.id, body });
  } else if (!ours && !ok) {
    await github.rest.issues.createComment({ owner, repo, issue_number: number, body });
  }
  core.info(ok ? 'CLA: everyone has signed.' : `CLA: waiting (${missing.map((u) => u.login).join(', ')}).`);
};
