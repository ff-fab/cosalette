## Epic COS-0k3 Complete: Package-Level Downstream AI Bootstrap/Help Surface

Phase 2 completed the installed-user AI bootstrap/help surface for cosalette by wiring package-level CLI entry points, compact runtime help topics, and updated package CLI tests. Review finished as approved with minor recommendations for consistency only.

**Files created/changed:**
- pyproject.toml
- packages/src/cosalette/_package_cli.py
- packages/src/cosalette/assets/guidance/cosalette.instructions.md
- packages/tests/unit/test_package_cli.py

**Functions created/changed:**
- ai_init
- ai_prime
- ai_help
- init_alias
- prime_alias
- _get_telemetry_help
- _get_testing_help
- _get_configuration_help
- _get_architecture_help
- main_cli

**Tests created/changed:**
- packages/tests/unit/test_package_cli.py updated and extended for help topics and wording

**Review Status:** APPROVED with minor recommendations

**Git Commit Message:**
docs: log COS-0k3 package AI bootstrap completion

- record Phase 2 completion for the installed-user bootstrap/help surface
- capture the package CLI files and help-topic coverage updated in the phase
- note approved review status with minor consistency recommendations only
