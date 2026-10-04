//! Golden health file cases shared with the Python check (pytest).

use std::fs;
use std::path::PathBuf;

use cosalette_health::{DEFAULT_FAIL_ON, check_health_file};
use serde_json::Value;

const CASES: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../packages/tests/fixtures/health_file_cases.json"
));

fn scratch_dir() -> PathBuf {
    let dir = std::env::temp_dir().join(format!("cosalette-health-golden-{}", std::process::id()));
    fs::create_dir_all(&dir).unwrap();
    dir
}

#[test]
fn golden_cases_match_the_contract() {
    let doc: Value = serde_json::from_str(CASES).unwrap();
    let cases = doc["cases"].as_array().unwrap();
    assert!(cases.len() >= 50, "fixture file looks truncated");
    let dir = scratch_dir();
    let mut failures = Vec::new();

    for (index, case) in cases.iter().enumerate() {
        // Arrange
        let name = case["name"].as_str().unwrap();
        let path = dir.join(format!("case-{index}.json"));
        if let Some(text) = case.get("text") {
            fs::write(&path, text.as_str().unwrap()).unwrap();
        } else if let Some(json) = case.get("json") {
            fs::write(&path, serde_json::to_string(json).unwrap()).unwrap();
        }
        let fail_on: Vec<String> = match case.get("fail_on") {
            Some(list) => list
                .as_array()
                .unwrap()
                .iter()
                .map(|status| status.as_str().unwrap().to_owned())
                .collect(),
            None => vec![DEFAULT_FAIL_ON.to_owned()],
        };
        let max_age = case.get("max_age").and_then(Value::as_f64);
        let now = case["now"].as_f64().unwrap();

        // Act
        let result = check_health_file(&path, now, max_age, &fail_on);

        // Assert
        let shown = path.display().to_string();
        let healthy = case["healthy"].as_bool().unwrap();
        let reason = match &result {
            Ok(reason) | Err(reason) => reason.clone(),
        };
        let reason_ok = match (case.get("reason"), case.get("reason_prefix")) {
            (Some(expected), _) => reason == expected.as_str().unwrap().replace("{path}", &shown),
            (None, Some(prefix)) => {
                reason.starts_with(&prefix.as_str().unwrap().replace("{path}", &shown))
            }
            (None, None) => panic!("case {name:?} has no reason"),
        };
        if result.is_ok() != healthy || !reason_ok {
            failures.push(format!("{name}: got {result:?}"));
        }
    }

    fs::remove_dir_all(&dir).unwrap();
    assert!(
        failures.is_empty(),
        "failing cases:\n{}",
        failures.join("\n")
    );
}
