# Pre-release polish: root devices, log rotation, reconnect backoff

*2026-02-21T09:32:09Z by Showboat 0.5.0*

Three features implemented before initial release: (1) Root device support — omit device name from decorators to publish at root-level MQTT topics ({prefix}/state), ideal for single-device apps. (2) Configurable log file rotation size — max_file_size_mb in LoggingSettings (default 10 MB). (3) Exponential reconnect backoff with ±20% jitter for MQTT connections, capped at reconnect_max_interval (default 300s).

Additional polish: removed MqttSettings.qos (hard-coded QoS 1), DRYed _publish_device_availability and _build_contexts via _all_registrations property, added bare-decorator test for @app.telemetry, fixed topic_prefix description spacing, cleaned up ErrorPublisher docstring.

```bash
cd /workspace && task test:unit 2>&1 | grep -oP '\d+ passed' | tail -1
```

```output
424 passed
```

```bash
cd /workspace && task lint 2>&1 | grep -E 'All checks|already formatted' && task typecheck 2>&1 | grep 'Success'
```

```output
All checks passed!
41 files already formatted
Success: no issues found in 18 source files
```
