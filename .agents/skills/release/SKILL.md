---
name: release
description: Use when cutting a release of this k8s-fastapi-starter service — bumping the version, generating the changelog and release notes, tagging vX.Y.Z, and pushing the tag that triggers the image build and GitHub release. Covers the git-cliff + Conventional Commits release flow.
---

# Cut a release

Read `SPEC.md` §8 (changelog/git-cliff) and §10 (CI/CD) first. Releases are
driven by Conventional Commits and git-cliff; `release.yml` fires on the `v*`
tag.

## Steps

1. **Verify Conventional Commits** — all commits since the last tag must follow
   Conventional Commits (git-cliff groups them into sections; `feat` → Features,
   `fix` → Bug Fixes, etc.; `chore(release)` and `chore(deps)` are skipped). If a
   commit message is wrong, fix history before releasing.

2. **Bump the changelog** —
   ```
   git cliff --bump -o CHANGELOG.md
   ```
   `--bump` computes the next semver from the commit types and writes the full
   `CHANGELOG.md`. (Use `uv run poe changelog`, which runs `git cliff -o CHANGELOG.md`,
   for a full regen without bumping.) Note the computed version as `vX.Y.Z`.

3. **Commit the release** —
   ```
   git add CHANGELOG.md
   git commit -m "chore(release): vX.Y.Z"
   ```

4. **Tag and push** —
   ```
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
   Push the tag (and the release commit on the branch).

5. **CI takes over** — `.github/workflows/release.yml` builds and pushes the
   image tagged with the version to GHCR and creates the GitHub release, with
   notes generated from:
   ```
   git cliff --latest --strip header
   ```
   You do not run that command by hand; it documents what the workflow uses.

See `AGENTS.md`, `cliff.toml`, and `SPEC.md` §8/§10 for the full details.
