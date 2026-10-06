# Contributing to VAV Ventilation Balancer

This file is for people working on the integration's code, not for end users.
For usage and configuration, see [README.md](README.md) / [README.ru.md](README.ru.md)
and [DOCS.md](DOCS.md) / [DOCS.ru.md](DOCS.ru.md).

## Before publishing a fork

`custom_components/vav_balancer/manifest.json` ships with placeholders that
must be replaced before you publish your own copy of this repository:

- `codeowners`: replace `@your-github-username` with your real GitHub handle
  (the `@`-prefixed format HACS and Home Assistant Core both expect).
- `documentation` / `issue_tracker`: replace with your repository's URL.

`hacs.json` at the repository root is required for [HACS](https://hacs.xyz)
to recognize this as a valid integration repository; `render_readme: true`
tells HACS to show `README.md` as the repository description.

## Repository layout

```
.
├── custom_components/vav_balancer/   # the integration itself
├── tests/components/vav_balancer/    # pytest suite (see below)
├── pytest.ini
├── hacs.json
├── dashboard_example.yaml
├── README.md / README.ru.md          # user-facing overview
├── DOCS.md / DOCS.ru.md              # full behavior reference
└── CONTRIBUTING.md                   # this file
```

## Running the tests

The suite uses [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component),
pinned to the same Home Assistant release this integration targets:

```bash
pip install pytest-homeassistant-custom-component
pytest tests/ --cov=custom_components.vav_balancer --cov-report=term-missing
```

Run this from the repository root (where `pytest.ini`, `custom_components/`
and `tests/` are siblings) — `tests/conftest.py` locates the integration
relative to its own path and symlinks it into the test harness's own
`testing_config/custom_components/`, so a different working directory will
fail to find it.

## Type checking

```bash
pip install mypy
mypy --python-version 3.13 --ignore-missing-imports custom_components/vav_balancer/*.py
```

The codebase currently passes plain `mypy` cleanly. It has not been run
against `--strict` (the bar for the quality scale's `strict-typing` /
Platinum rule) — treat that as a known gap, not a verified pass.

## Quality scale

This integration was built against Home Assistant's
[Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/).
It meets the Bronze tier in full and several Silver/Gold rules (see the
project's change history for the specific rules addressed and why a few —
`discovery`, `dynamic-devices`, `reauthentication-flow` — are structurally
not applicable to an integration that only references existing Home
Assistant entities rather than talking to an external device or service).
