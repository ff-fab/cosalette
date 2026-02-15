## Epic Phase 5 Complete: CLI Scaffolding

Added Typer-based CLI scaffolding via `build_cli()` factory that wraps `App.run()` with
framework-level options: `--version`, `--dry-run`, `--log-level`, `--log-format`,
`--env-file`. Exit codes differentiate success (0), config errors (1), and runtime
errors (3). 15 tests cover all flags, overrides, and error paths.

**Files created/changed:**

- `packages/src/cosalette/_cli.py` (NEW — 156 lines)
- `packages/src/cosalette/_app.py` (MODIFIED — added `description` param, refactored
  `run()` to delegate to Typer)
- `packages/tests/unit/test_cli.py` (NEW — 321 lines)
- `.pre-commit-config.yaml` (MODIFIED — added typer to mypy additional_dependencies)

**Functions created/changed:**

- `build_cli(app: App) -> typer.Typer` — CLI factory with framework options
- `App.__init__()` — added `description` parameter
- `App.run()` — refactored from `asyncio.run` to Typer delegation

**Tests created/changed:**

- `TestVersionFlag::test_version_prints_name_and_version`
- `TestHelpFlag::test_help_shows_description_and_powered_by`
- `TestHelpFlag::test_help_shows_all_expected_options`
- `TestDryRunFlag::test_dry_run_flag_sets_app_dry_run`
- `TestDryRunFlag::test_default_dry_run_is_false`
- `TestEnvFileFlag::test_env_file_flag_changes_settings_source`
- `TestEnvFileFlag::test_default_env_file_is_dot_env`
- `TestLogLevelOverride::test_log_level_overrides_settings`
- `TestLogLevelOverride::test_invalid_log_level_returns_error`
- `TestLogFormatOverride::test_log_format_overrides_settings`
- `TestLogFormatOverride::test_invalid_log_format_returns_error`
- `TestExitCodes::test_clean_run_exits_zero`
- `TestExitCodes::test_config_error_exits_one`
- `TestExitCodes::test_exit_code_constants_have_expected_values`
- `TestExitCodes::test_runtime_error_exits_three`

**Review Status:** APPROVED with fixes applied

Review findings addressed:

1. Added `test_runtime_error_exits_three` (runtime error exit code was untested)
2. Changed `EXIT_RUNTIME_ERROR` from 2 → 3 (avoids Click/Typer exit code 2 collision)
3. Moved `Settings` import to `TYPE_CHECKING` block
4. Replaced `AsyncMock` with `MagicMock` for synchronous `_settings_class` mock
   (eliminated `PytestUnraisableExceptionWarning`)
5. Added `typer>=0.12` to pre-commit mypy `additional_dependencies`

**Git Commit Message:**

```
feat: add CLI scaffolding with Typer

- Add build_cli() factory with --version, --dry-run, --log-level,
  --log-format, --env-file options
- Refactor App.run() to delegate to Typer CLI
- Add App description parameter for CLI help text
- Exit codes: 0 (ok), 1 (config error), 3 (runtime error)
- 15 new tests covering all CLI flags and error paths
```
