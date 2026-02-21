# Changelog

## 0.1.0 (2026-02-21)


### ⚠ BREAKING CHANGES

* API ergonomics - app.run(), lifespan, injection ([#22](https://github.com/ff-fab/cosalette/issues/22))

### Features

* :sparkles: initial commit ([03e1dfc](https://github.com/ff-fab/cosalette/commit/03e1dfc320118a308a7def5e26adb7056d8e6d3b))
* add [@app](https://github.com/app).command() decorator for command handler registration ([#23](https://github.com/ff-fab/cosalette/issues/23)) ([9e09765](https://github.com/ff-fab/cosalette/commit/9e097654bd29638139f1295513ea2bf1013ad6d8))
* add CLI scaffolding with Typer ([#9](https://github.com/ff-fab/cosalette/issues/9)) ([4a92f9a](https://github.com/ff-fab/cosalette/commit/4a92f9a76dabd6ffcd735b9f52beba0d6596137e))
* add cosalette.testing module (Phase 6) ([#11](https://github.com/ff-fab/cosalette/issues/11)) ([fad9d74](https://github.com/ff-fab/cosalette/commit/fad9d74526e5c7e1e938276c27856ba95deb3f3b))
* add error publisher and health reporter (_errors.py, _health.py) ([#5](https://github.com/ff-fab/cosalette/issues/5)) ([5c1599a](https://github.com/ff-fab/cosalette/commit/5c1599a326cce50bf5c157a650e4f8b61aabe8b1))
* add MQTT client port and adapters (_mqtt.py) ([#3](https://github.com/ff-fab/cosalette/issues/3)) ([7fc3efa](https://github.com/ff-fab/cosalette/commit/7fc3efaee14fc80912b9169429becc08b2feb560))
* add periodic heartbeat scheduling (opt-in, default 60s) ([#19](https://github.com/ff-fab/cosalette/issues/19)) ([f41fca8](https://github.com/ff-fab/cosalette/commit/f41fca85ed5ac829165e705f3f8137dcc95d6ada))
* API ergonomics - app.run(), lifespan, injection ([#22](https://github.com/ff-fab/cosalette/issues/22)) ([4fe51d6](https://github.com/ff-fab/cosalette/commit/4fe51d6bb14c9b3956ee7413718c8f8561babf08))
* migrate documentation from MkDocs to Zensical ([#21](https://github.com/ff-fab/cosalette/issues/21)) ([674832c](https://github.com/ff-fab/cosalette/commit/674832c1d765a0dffdabf9d740b44e899155e157))
* optional MQTT params in [@app](https://github.com/app).command() + docs update ([#25](https://github.com/ff-fab/cosalette/issues/25)) ([d1a4f25](https://github.com/ff-fab/cosalette/commit/d1a4f253dc2e6b366d99a527467b908accac2370))
* Phase 1 — Foundation modules (Settings, Clock, Logging) ([#2](https://github.com/ff-fab/cosalette/issues/2)) ([3a7d4c0](https://github.com/ff-fab/cosalette/commit/3a7d4c0d8ca1edf8c91789ea4e3ad20efa697e22))
* Phase 4 — App orchestrator, DeviceContext, and TopicRouter ([#8](https://github.com/ff-fab/cosalette/issues/8)) ([2ca3817](https://github.com/ff-fab/cosalette/commit/2ca38178ca4506c0ceff268f1dff9537a7ffcb95))
* pre-release polish — root devices, log rotation, reconnect backoff ([#27](https://github.com/ff-fab/cosalette/issues/27)) ([fb641d6](https://github.com/ff-fab/cosalette/commit/fb641d6bd099c89bdec06a7a59cbe2bea05abb6d))
* public API, gate tasks, and integration tests (Phase 7) ([#12](https://github.com/ff-fab/cosalette/issues/12)) ([47e75fb](https://github.com/ff-fab/cosalette/commit/47e75fb3ff1bdf159fd9ff55dc2eb0a6ca38549c))
* telemetry error deduplication and health integration ([#29](https://github.com/ff-fab/cosalette/issues/29)) ([80a75bb](https://github.com/ff-fab/cosalette/commit/80a75bb342a46f7bd7d6cd00e592b128c74fd294))


### Bug Fixes

* improve error handling in device proxy and lifespan teardown ([#24](https://github.com/ff-fab/cosalette/issues/24)) ([7c75c27](https://github.com/ff-fab/cosalette/commit/7c75c27c75a5d8488ffe4b2d2d02166176966d95))
* isolate make_settings from ambient environment variables ([#13](https://github.com/ff-fab/cosalette/issues/13)) ([c2d0751](https://github.com/ff-fab/cosalette/commit/c2d0751420b110003d5774eb0466b73528c96c39))
