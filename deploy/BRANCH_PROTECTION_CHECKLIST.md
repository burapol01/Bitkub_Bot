# GitHub Branch Protection Checklist

Use this checklist to enforce the safer flow:

1. Work on a branch
2. Open a PR into `main`
3. Wait for `CI` to pass
4. Merge into `main`
5. Let deploy run after the successful `CI` run on `main`

## 1. Create A Rule For `main`

In GitHub:

- `Settings`
- `Branches`
- `Add branch protection rule`
- Branch name pattern: `main`

## 2. Require Pull Requests

Enable:

- `Require a pull request before merging`
- `Require approvals`
  - recommended starting point: `1`
- `Dismiss stale pull request approvals when new commits are pushed`

This keeps direct pushes to `main` from being the normal path.

## 3. Require CI Before Merge

Enable:

- `Require status checks to pass before merging`
- `Require branches to be up to date before merging`

After the new workflow runs once, add this required check:

- `CI / Validate`

Do not require the deploy workflow here. Deploy happens after merge.

## 4. Keep History Safe

Enable:

- `Require conversation resolution before merging`
- `Do not allow bypassing the above settings`
- `Block force pushes`
- `Block branch deletion`

Optional but recommended:

- `Require linear history`

## 5. Restrict Who Can Push To `main`

If your repository plan supports it, enable restrictions so only admins or selected maintainers can bypass rules when absolutely necessary.

For normal work, use PRs only.

## 6. Recommended Merge Policy

Recommended simple policy:

- allow `Squash merge` or `Rebase merge`
- avoid ordinary merge commits if you want a cleaner history

If you already prefer fast-forward only or a linear history style, keep that consistent with your team workflow.

## 7. First End-To-End Test

After branch protection is saved:

1. Push a small change to a feature branch
2. Open a PR into `main`
3. Confirm `CI / Validate` passes
4. Merge the PR
5. Confirm `CI` runs on `main`
6. Confirm deploy starts only after that `CI` run succeeds

## 8. Notes

- If `CI` is marked as required before it has ever run, GitHub may not list it yet. Run one PR first, then return and select the check.
- Keep deploy secrets and environment protection under GitHub `Settings > Environments > production` if you want stronger release controls later.
- This setup is intentionally simple and works well for a single DigitalOcean droplet.
