//! `cosalette-health`: exit 0 when the app's health file is fresh, 1 otherwise.

use std::io::Write;
use std::process::ExitCode;
use std::time::{SystemTime, UNIX_EPOCH};

use cosalette_health::{HEALTH_FILE_ENV, Outcome, run};

fn main() -> ExitCode {
    // Docker HEALTHCHECK reserves exit 2 and the release profile aborts on
    // panic, so a bug must still report "unhealthy" with exit 1.
    std::panic::set_hook(Box::new(|info| {
        let _ = writeln!(std::io::stderr(), "unhealthy: internal error: {info}");
        std::process::exit(1);
    }));
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0.0, |elapsed| elapsed.as_secs_f64());
    let outcome = run(
        std::env::args_os().skip(1),
        std::env::var_os(HEALTH_FILE_ENV),
        now,
    );
    // Write errors (e.g. a closed pipe) must not change the exit code.
    match outcome {
        Outcome::Help(text) => {
            let _ = std::io::stdout().write_all(text.as_bytes());
            ExitCode::SUCCESS
        }
        Outcome::Healthy(reason) => {
            let _ = writeln!(std::io::stdout(), "{reason}");
            ExitCode::SUCCESS
        }
        Outcome::Unhealthy(reason) => {
            let _ = writeln!(std::io::stderr(), "unhealthy: {reason}");
            ExitCode::from(1)
        }
    }
}
