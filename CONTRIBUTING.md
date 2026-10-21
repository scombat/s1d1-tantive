# Contributing

This page shows how to ask a question, report a bug or propose a change to s1d1-tantive. Everyone taking part follows
the [code of conduct](CODE_OF_CONDUCT.md).

## Questions and bugs

Open an [issue](https://github.com/scombat/s1d1-tantive/issues/new/choose) with the *Bug report* or *Question* form:
it asks for the version or image tag, the variant, the deployment target, what you did, what you saw and what you
expected. A vulnerability never goes in an issue: report it privately as [SECURITY.md](SECURITY.md) says.

## Propose a change

1. Fork the repository and branch from `main`.
2. Install the tools and the dependencies:

   ```bash
   uv sync --all-groups
   ```

3. Make the change, with its tests, and keep the checks green:

   ```bash
   uv run ruff check . && uv run pytest -m "not slow"
   ```

4. Add one bullet under `## Unreleased` in [CHANGELOG.md](CHANGELOG.md), and update the docs the change touches.
5. Commit with a subject in the style of `git log --oneline`: `type(scope): what changes`, in the present tense and
   lower case, with `feat`, `fix`, `docs`, `test`, `ci`, `build`, `refactor` or `chore` as type, the scope optional and
   a `!` after it for a breaking change (`feat(deploy)!: drop Railway as a deployment target`).
6. Open a pull request against `main`; its template lists what a review checks.

## Licence of contributions

s1d1-tantive is under the [Business Source License 1.1](LICENSE). By opening a pull request, you agree that your
contribution is licensed under the same licence as the repository.

## Further

How to release, refresh the pinned weights, add a format or a deploy target: [MAINTAINERS.md](MAINTAINERS.md).
