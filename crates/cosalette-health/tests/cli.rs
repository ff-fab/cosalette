//! End-to-end tests of the `cosalette-health` binary: exit codes and output.

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output};
use std::time::{SystemTime, UNIX_EPOCH};

const ENV: &str = "COSALETTE_HEALTH_FILE";

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64()
}

/// Write a health file that is `age` seconds old into a per-test directory.
fn health_file(test: &str, age: f64, extra: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "cosalette-health-cli-{}-{test}",
        std::process::id()
    ));
    fs::create_dir_all(&dir).unwrap();
    let path = dir.join("health.json");
    let written_at = now() - age;
    fs::write(
        &path,
        format!(r#"{{"written_at": {written_at}, "interval": 10{extra}}}"#),
    )
    .unwrap();
    path
}

fn probe(args: &[&str], env: Option<&str>) -> Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_cosalette-health"));
    command.args(args).env_remove(ENV);
    if let Some(value) = env {
        command.env(ENV, value);
    }
    command.output().unwrap()
}

fn stdout(output: &Output) -> String {
    String::from_utf8_lossy(&output.stdout).into_owned()
}

fn stderr(output: &Output) -> String {
    String::from_utf8_lossy(&output.stderr).into_owned()
}

#[test]
fn fresh_file_exits_0_with_reason_on_stdout() {
    let path = health_file("fresh", 1.0, "");

    let output = probe(&["--file", path.to_str().unwrap()], None);

    assert_eq!(output.status.code(), Some(0));
    assert!(stdout(&output).starts_with("healthy (health file "));
    assert!(stderr(&output).is_empty());
}

#[test]
fn env_var_names_the_file() {
    let path = health_file("env", 1.0, "");

    let output = probe(&[], Some(&format!("  {}\n", path.display())));

    assert_eq!(output.status.code(), Some(0), "{}", stderr(&output));
}

#[test]
fn file_option_wins_over_env_var() {
    let path = health_file("precedence", 1.0, "");

    let output = probe(
        &["--file", path.to_str().unwrap()],
        Some("/nonexistent/health.json"),
    );

    assert_eq!(output.status.code(), Some(0), "{}", stderr(&output));
}

#[test]
fn missing_file_exits_1_with_reason_on_stderr() {
    let output = probe(&["--file", "/nonexistent/health.json"], None);

    assert_eq!(output.status.code(), Some(1));
    assert_eq!(
        stderr(&output),
        "unhealthy: health file /nonexistent/health.json does not exist\n"
    );
    assert!(stdout(&output).is_empty());
}

#[test]
fn no_file_configured_exits_1() {
    for (args, env) in [
        (&[][..], None),
        (&[][..], Some("  ")),
        (&["--file="][..], None),
    ] {
        let output = probe(args, env);

        assert_eq!(output.status.code(), Some(1));
        assert_eq!(
            stderr(&output),
            "unhealthy: no health file (pass --file or set COSALETTE_HEALTH_FILE)\n"
        );
    }
}

#[test]
fn default_max_age_is_three_intervals() {
    // interval 10 -> limit 30s; stay well clear of the boundary for wall-clock slack.
    let fresh = health_file("default-fresh", 25.0, "");
    let stale = health_file("default-stale", 35.0, "");

    assert_eq!(
        probe(&["--file", fresh.to_str().unwrap()], None)
            .status
            .code(),
        Some(0)
    );
    let output = probe(&["--file", stale.to_str().unwrap()], None);
    assert_eq!(output.status.code(), Some(1));
    assert!(
        stderr(&output).ends_with("(max age 30s)\n"),
        "{}",
        stderr(&output)
    );
}

#[test]
fn max_age_option_overrides_the_interval() {
    let path = health_file("max-age", 100.0, "");
    let file = path.to_str().unwrap();

    assert_eq!(
        probe(&["--file", file, "--max-age", "105"], None)
            .status
            .code(),
        Some(0)
    );
    assert_eq!(
        probe(&["--file", file, "--max-age=95"], None).status.code(),
        Some(1)
    );
}

#[test]
fn fail_on_is_repeatable() {
    let path = health_file(
        "fail-on",
        1.0,
        r#", "devices": {"a": {"status": "stale"}, "b": {"status": "error"}}"#,
    );
    let file = path.to_str().unwrap();

    let default = probe(&["--file", file], None);
    let custom = probe(
        &["--file", file, "--fail-on", "error", "--fail-on=stale"],
        None,
    );
    let other = probe(&["--file", file, "--fail-on", "offline"], None);

    assert_eq!(stderr(&default), "unhealthy: failing devices: a=stale\n");
    assert_eq!(
        stderr(&custom),
        "unhealthy: failing devices: a=stale, b=error\n"
    );
    assert_eq!(other.status.code(), Some(0));
}

#[test]
fn usage_errors_exit_1_never_2() {
    let cases: &[&[&str]] = &[
        &["--bogus"],
        &["positional"],
        &["--file"],
        &["--max-age"],
        &["--fail-on"],
        &["--max-age", "-1"],
        &["--max-age", "abc"],
        &["--max-age", "inf"],
        &["--max-age", "NaN"],
        &["--max-age=", "--file", "x"],
        &["--max", "5"],
        &["--file", "--max-age"],
    ];
    for args in cases {
        let output = probe(args, Some("/nonexistent/health.json"));

        assert_eq!(output.status.code(), Some(1), "{args:?}");
        assert!(
            stderr(&output).starts_with("unhealthy: usage error: "),
            "{args:?}: {}",
            stderr(&output)
        );
    }
}

#[test]
fn help_exits_0() {
    for flag in ["-h", "--help"] {
        let output = probe(&[flag], None);

        assert_eq!(output.status.code(), Some(0));
        assert!(stdout(&output).starts_with("usage: cosalette-health"));
    }
}
