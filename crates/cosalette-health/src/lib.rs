//! Native `cosalette-health` liveness probe (ADR-087).
//!
//! Checks the health file a cosalette app writes (ADR-083) against the
//! contract in `docs/reference/health-file.md`. The Python fallback in
//! `cosalette._health._probe` implements the same rules; the golden
//! fixtures in `packages/tests/fixtures/health_file_cases.json` keep both
//! in step.

use std::ffi::OsString;
use std::fs;
use std::io::ErrorKind;
use std::path::Path;

use serde_json::{Map, Value};

/// Environment variable naming the health file.
pub const HEALTH_FILE_ENV: &str = "COSALETTE_HEALTH_FILE";
/// Contract major version written to, and accepted from, `health_file_version`.
pub const HEALTH_FILE_VERSION: u64 = 1;
/// Write interval in seconds assumed when the file has no usable `interval`.
pub const DEFAULT_HEALTH_FILE_INTERVAL: f64 = 60.0;
/// Device status that fails the probe unless `--fail-on` says otherwise.
pub const DEFAULT_FAIL_ON: &str = "stale";
/// Default `--max-age` as a multiple of the file's write interval.
pub const MAX_AGE_FACTOR: f64 = 3.0;

const NO_FILE_REASON: &str = "no health file (pass --file or set COSALETTE_HEALTH_FILE)";

const HELP: &str = "\
usage: cosalette-health [-h] [--file FILE] [--max-age MAX_AGE] [--fail-on FAIL_ON]

Check a cosalette health file for a container liveness probe.

options:
  -h, --help         show this help message and exit
  --file FILE        Health file written by the app. Default: $COSALETTE_HEALTH_FILE.
  --max-age MAX_AGE  Seconds after which the file counts as too old. Default: 3 x the
                     write interval recorded in the file.
  --fail-on FAIL_ON  Device status that makes the check fail; repeatable. Default: stale.

Exits 0 when healthy and 1 otherwise, with the reason on stderr.
";

/// What the process prints and how it exits.
#[derive(Debug, PartialEq)]
pub enum Outcome {
    /// `--help`: print the text on stdout, exit 0.
    Help(&'static str),
    /// Healthy: print the reason on stdout, exit 0.
    Healthy(String),
    /// Unhealthy or a usage error: print `unhealthy: <reason>` on stderr, exit 1.
    Unhealthy(String),
}

/// Parsed command line.
#[derive(Debug, Default, PartialEq)]
pub struct Options {
    pub file: Option<OsString>,
    pub max_age: Option<f64>,
    pub fail_on: Vec<String>,
}

/// Run the probe for `args` (without the program name) at Unix time `now`.
pub fn run<I>(args: I, env_file: Option<OsString>, now: f64) -> Outcome
where
    I: IntoIterator<Item = OsString>,
{
    let options = match parse_args(args) {
        Ok(Some(options)) => options,
        Ok(None) => return Outcome::Help(HELP),
        Err(message) => return Outcome::Unhealthy(format!("usage error: {message}")),
    };
    let file = options
        .file
        .filter(|file| !file.is_empty())
        .or_else(|| env_file.map(trim).filter(|file| !file.is_empty()));
    let Some(file) = file else {
        return Outcome::Unhealthy(NO_FILE_REASON.to_owned());
    };
    let fail_on = if options.fail_on.is_empty() {
        vec![DEFAULT_FAIL_ON.to_owned()]
    } else {
        options.fail_on
    };
    match check_health_file(Path::new(&file), now, options.max_age, &fail_on) {
        Ok(reason) => Outcome::Healthy(reason),
        Err(reason) => Outcome::Unhealthy(reason),
    }
}

/// Parse the arguments like the Python fallback's argparse parser.
///
/// Returns `Ok(None)` for `--help`.
pub fn parse_args<I>(args: I) -> Result<Option<Options>, String>
where
    I: IntoIterator<Item = OsString>,
{
    let mut options = Options::default();
    let mut args = args.into_iter();
    while let Some(arg) = args.next() {
        let Some(text) = arg.to_str() else {
            return Err(format!("unrecognized arguments: {}", arg.to_string_lossy()));
        };
        let (name, inline) = match text.split_once('=') {
            Some((name, value)) if name.starts_with("--") => (name, Some(value)),
            _ => (text, None),
        };
        if matches!(name, "-h" | "--help") && inline.is_none() {
            return Ok(None);
        }
        if !matches!(name, "--file" | "--max-age" | "--fail-on") {
            return Err(format!("unrecognized arguments: {text}"));
        }
        let value = match inline {
            Some(value) => OsString::from(value),
            None => args
                .next()
                .filter(|value| !value.to_str().is_some_and(is_option))
                .ok_or_else(|| format!("argument {name}: expected one argument"))?,
        };
        match name {
            "--file" => options.file = Some(value),
            "--max-age" => options.max_age = Some(parse_max_age(&value)?),
            _ => options.fail_on.push(
                value
                    .into_string()
                    .map_err(|value| format!("invalid --fail-on {}", value.to_string_lossy()))?,
            ),
        }
    }
    Ok(Some(options))
}

fn is_option(value: &str) -> bool {
    value.starts_with('-') && value.len() > 1 && value.parse::<f64>().is_err()
}

fn trim(value: OsString) -> OsString {
    match value.to_str() {
        Some(text) => OsString::from(text.trim()),
        None => value,
    }
}

/// Parse `--max-age`: a finite number of seconds, at least 0.
pub fn parse_max_age(raw: &std::ffi::OsStr) -> Result<f64, String> {
    raw.to_str()
        .and_then(|text| text.parse::<f64>().ok())
        .filter(|value| value.is_finite() && *value >= 0.0)
        .ok_or_else(|| {
            format!(
                "argument --max-age: '{}' is not a finite number >= 0",
                raw.to_string_lossy()
            )
        })
}

/// Check the health file at `path` against `now` (Unix time).
///
/// `Ok` carries the healthy reason, `Err` the unhealthy one.
pub fn check_health_file(
    path: &Path,
    now: f64,
    max_age: Option<f64>,
    fail_on: &[String],
) -> Result<String, String> {
    let shown = path.display();
    let text = fs::read_to_string(path).map_err(|err| match err.kind() {
        ErrorKind::NotFound => format!("health file {shown} does not exist"),
        _ => format!("health file {shown} is unreadable: {err}"),
    })?;
    let value: Value = serde_json::from_str(&text)
        .map_err(|err| format!("health file {shown} is unreadable: {err}"))?;
    let Value::Object(data) = value else {
        return Err(format!("health file {shown} is not a JSON object"));
    };
    match data.get("health_file_version") {
        None => {}
        Some(Value::Number(version)) if version.is_i64() || version.is_u64() => {
            if version.as_u64() != Some(HEALTH_FILE_VERSION) {
                return Err(format!(
                    "health file {shown} has unsupported version {version}"
                ));
            }
        }
        Some(_) => return Err(format!("health file {shown} has an invalid version")),
    }
    let Some(written_at) = number(data.get("written_at")) else {
        return Err(format!("health file {shown} has no written_at time"));
    };
    let limit = max_age.unwrap_or_else(|| default_max_age(&data));
    let age = now - written_at;
    if age > limit {
        return Err(format!(
            "health file is {}s old (max age {}s)",
            whole(age),
            whole(limit)
        ));
    }
    let failing = failing_devices(data.get("devices"), fail_on);
    if !failing.is_empty() {
        return Err(format!("failing devices: {}", failing.join(", ")));
    }
    Ok(format!("healthy (health file {}s old)", whole(age)))
}

fn number(value: Option<&Value>) -> Option<f64> {
    value
        .and_then(Value::as_f64)
        .filter(|number| number.is_finite())
}

fn default_max_age(data: &Map<String, Value>) -> f64 {
    let interval = number(data.get("interval"))
        .filter(|interval| *interval > 0.0)
        .unwrap_or(DEFAULT_HEALTH_FILE_INTERVAL);
    MAX_AGE_FACTOR * interval
}

fn failing_devices(devices: Option<&Value>, fail_on: &[String]) -> Vec<String> {
    let Some(Value::Object(devices)) = devices else {
        return Vec::new();
    };
    // serde_json's map is a BTreeMap: names come out sorted, like the Python check.
    devices
        .iter()
        .filter_map(|(name, entry)| match entry.get("status") {
            Some(Value::String(status)) if fail_on.contains(status) => {
                Some(format!("{name}={status}"))
            }
            _ => None,
        })
        .collect()
}

/// Format seconds like Python's `f"{x:.0f}"`: round half to even.
fn whole(seconds: f64) -> String {
    format!("{:.0}", seconds.round_ties_even())
}
