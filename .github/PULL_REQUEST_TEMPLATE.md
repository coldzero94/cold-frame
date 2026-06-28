<!-- Thanks for contributing to coldframe! Keep it small + tested. -->

## What & why

<!-- One or two sentences: what this changes and the motivation. Link any issue. -->

## Checklist

- [ ] The offline gate is green: `ruff check .` + `mypy cold_frame` + `pytest -m "not slow"`
- [ ] Tests added/updated in the same commit (TDD — see CONTRIBUTING.md)
- [ ] No new core dependency (anything beyond `pydantic`+`numpy` is behind an extra + an ADR)
- [ ] If an invariant (CLAUDE.md §3) or a contract changed, there's an ADR in `docs/decisions.md`
