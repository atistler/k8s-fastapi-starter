# Releasing

The changelog and release notes come from **Conventional Commits** through **git-cliff** (SPEC section 8).

## Commit messages

Use the form `type(scope): imperative summary`. Examples:

```text
feat(items): add delete endpoint
fix(readyz): return 503 when redis ping times out
chore(deps): bump fastapi
```

| Type | Changelog section |
|---|---|
| `feat` | Features |
| `fix` | Bug Fixes |
| `perf` | Performance |
| `refactor` | Refactoring |
| `docs` | Documentation |
| `build`, `ci`, `test`, `chore` | Build, CI, Tests, Chores |
| `chore(release)`, `chore(deps)` | Skipped |

A breaking change uses `!` after the type, or a `BREAKING CHANGE:` footer. These are marked in the changelog.

`conventional-pre-commit` rejects messages that do not follow the format, when the commit-msg hook is installed (`uv run poe hooks`).

## Cut a release

```bash
uv run poe changelog                      # optional: preview the full changelog in CHANGELOG.md
git cliff --bump -o CHANGELOG.md    # computes the next semver from the commits
git add CHANGELOG.md               # plus any version file the project keeps
git commit -m "chore(release): vX.Y.Z"
git tag vX.Y.Z
git push origin main vX.Y.Z
```

Pushing the tag starts `.github/workflows/release.yml`. It:

1. Checks out the full history (`fetch-depth: 0`).
2. Builds and pushes the image to GHCR, tagged with the version and `latest-release`.
3. Generates release notes with `git cliff --latest --strip header`.
4. Creates the GitHub release with those notes.

Do not move or re-create a tag that was already pushed. Cut a new version instead.

## Notes

- `tag_pattern` is `v[0-9]*`, so only tags like `v1.2.3` count as releases.
- The `chore(release)` commit is excluded from the notes, so it does not appear in its own release.
- A squash-merge keeps the PR title as the commit subject. Write PR titles in Conventional Commits form.
