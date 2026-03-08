# Changelog

## [0.1.9](https://github.com/ff-fab/cosalette/compare/v0.1.8...v0.1.9) (2026-03-08)


### Features

* add clock-controlled sleep to ClockPort protocol ([#85](https://github.com/ff-fab/cosalette/issues/85)) ([021564b](https://github.com/ff-fab/cosalette/commit/021564bb0363691dae50bd98a4b1c83ebcaaee63))
* add cognitive complexity gate and refactor all violations below threshold ([9c2d88e](https://github.com/ff-fab/cosalette/commit/9c2d88ec67403268db5358882858de9add8ca379)), closes [#71](https://github.com/ff-fab/cosalette/issues/71)
* add MQTT integration tests with testcontainers (COS-0ky) ([#90](https://github.com/ff-fab/cosalette/issues/90)) ([edc45b4](https://github.com/ff-fab/cosalette/commit/edc45b457011c8ee8bdc7df0baa72ee7a67961a6))
* add property-based tests with Hypothesis (COS-rmy) ([#88](https://github.com/ff-fab/cosalette/issues/88)) ([f285c1b](https://github.com/ff-fab/cosalette/commit/f285c1be9714a6e20de9c8ac3f60629b2b0b8605))
* add registry introspection module (_introspect.py) ([#91](https://github.com/ff-fab/cosalette/issues/91)) ([4fa25ef](https://github.com/ff-fab/cosalette/commit/4fa25ef04edf66515d61f059876471090c27d334))
* adopt orjson as hard dependency (COS-gjp) ([#87](https://github.com/ff-fab/cosalette/issues/87)) ([4f123ee](https://github.com/ff-fab/cosalette/commit/4f123ee450b7f5633adb5afc9100edd540f13cdb))
* export composite strategy types and add strategies re-export module ([#84](https://github.com/ff-fab/cosalette/issues/84)) ([5ac8946](https://github.com/ff-fab/cosalette/commit/5ac894692e121b90387dd2a1c6cfe073f7ca47a7))

## [0.1.8](https://github.com/ff-fab/cosalette/compare/v0.1.7...v0.1.8) (2026-03-05)


### Features

* add IntervalSpec type for deferred telemetry interval resolution ([#68](https://github.com/ff-fab/cosalette/issues/68)) ([8bf5733](https://github.com/ff-fab/cosalette/commit/8bf5733aa9497b8f07c5e35d942ace1984bd65d7))

## [0.1.7](https://github.com/ff-fab/cosalette/compare/v0.1.6...v0.1.7) (2026-03-04)


### Features

* scoped name uniqueness for shared telemetry+command topics ([#66](https://github.com/ff-fab/cosalette/issues/66)) ([a489a3e](https://github.com/ff-fab/cosalette/commit/a489a3e494c560c6b983a513156fb2857ff8392e))

## [0.1.6](https://github.com/ff-fab/cosalette/compare/v0.1.5...v0.1.6) (2026-03-03)


### Features

* add telemetry coalescing groups ([#62](https://github.com/ff-fab/cosalette/issues/62)) ([d140658](https://github.com/ff-fab/cosalette/commit/d1406585f9f33a647d7dde1dc0b15ac2930c6e83))

## [0.1.5](https://github.com/ff-fab/cosalette/compare/v0.1.4...v0.1.5) (2026-02-27)


### Features

* add adapters= dict parameter to App constructor ([#53](https://github.com/ff-fab/cosalette/issues/53)) ([a9e54af](https://github.com/ff-fab/cosalette/commit/a9e54af08e345e16f6c113f6b770e3b34892da82))
* add enabled= parameter for conditional device registration ([#52](https://github.com/ff-fab/cosalette/issues/52)) ([47389f4](https://github.com/ff-fab/cosalette/commit/47389f476007c39f9cd43aabfa3146c5a2bfad4b))
* add imperative add_device/add_telemetry/add_command methods ([#51](https://github.com/ff-fab/cosalette/issues/51)) ([180e75c](https://github.com/ff-fab/cosalette/commit/180e75cfeb449d35a74c13329cecc6e3dabfe848))
* add persistence system with Store protocol, DeviceStore, and save policies ([#47](https://github.com/ff-fab/cosalette/issues/47)) ([c5528e3](https://github.com/ff-fab/cosalette/commit/c5528e3ea6231315ba8e6772955c2ffe5bbecf76))
* auto-manage adapter lifecycle via async context manager protocol ([#49](https://github.com/ff-fab/cosalette/issues/49)) ([c1b0843](https://github.com/ff-fab/cosalette/commit/c1b0843da74ec8c3592e9d0003957aba7eec4e5c))
* settings-aware adapter constructors with unified DI pipeline ([#50](https://github.com/ff-fab/cosalette/issues/50)) ([65436ba](https://github.com/ff-fab/cosalette/commit/65436ba7354420c9273a40fd4b095e2f66f7f4d6))


### Bug Fixes

* cancel adapter entry on shutdown signal ([#56](https://github.com/ff-fab/cosalette/issues/56)) ([4606b21](https://github.com/ff-fab/cosalette/commit/4606b21ed0579a673bfe2c25b104a90db405155a))
* drop file: prefix from syft command to fix glob expansion ([#59](https://github.com/ff-fab/cosalette/issues/59)) ([caa03e6](https://github.com/ff-fab/cosalette/commit/caa03e61e3151820e232aae8bd9d2e54cbde8bcd))
* install signal handlers before adapter lifecycle entry ([#55](https://github.com/ff-fab/cosalette/issues/55)) ([29d5351](https://github.com/ff-fab/cosalette/commit/29d535132d2a18dfd291707268d1587631eed131))

## [0.1.4](https://github.com/ff-fab/cosalette/compare/v0.1.3...v0.1.4) (2026-02-23)


### Features

* add init= callback for per-device state injection ([#46](https://github.com/ff-fab/cosalette/issues/46)) ([3192267](https://github.com/ff-fab/cosalette/commit/3192267c30f56cf55a9b0dc610d20edfaaf3c987))
* eagerly instantiate settings in App.__init__ ([#44](https://github.com/ff-fab/cosalette/issues/44)) ([59d1b6f](https://github.com/ff-fab/cosalette/commit/59d1b6fea8a82956b810498db51b706869fcefd0))

## [0.1.3](https://github.com/ff-fab/cosalette/compare/v0.1.2...v0.1.3) (2026-02-22)


### Features

* signal filters utility library (Pt1Filter, MedianFilter, OneEuroFilter) ([#41](https://github.com/ff-fab/cosalette/issues/41)) ([15c3ace](https://github.com/ff-fab/cosalette/commit/15c3acef11bec302b61367bea57bd302bb80b389))

## [0.1.2](https://github.com/ff-fab/cosalette/compare/v0.1.1...v0.1.2) (2026-02-22)


### Features

* add publish strategies for telemetry devices ([#38](https://github.com/ff-fab/cosalette/issues/38)) ([751b35e](https://github.com/ff-fab/cosalette/commit/751b35eaacb4e6957dcd04470f442feb30ce0bc2))
* recursive leaf-level thresholds and strategy documentation ([#40](https://github.com/ff-fab/cosalette/issues/40)) ([3c7f9cd](https://github.com/ff-fab/cosalette/commit/3c7f9cdcbc2804e2278df1f29f90b4fcaa161fba))

## [0.1.1](https://github.com/ff-fab/cosalette/compare/v0.1.0...v0.1.1) (2026-02-22)

### Features

- adapter factory settings injection
  ([#36](https://github.com/ff-fab/cosalette/issues/36))
  ([12d14aa](https://github.com/ff-fab/cosalette/commit/12d14aa2a0d671f0c684c87a3f180451b18cc599))

## 0.1.0 (2026-02-21)

### ⚠ BREAKING CHANGES

- API ergonomics - app.run(), lifespan, injection
  ([#22](https://github.com/ff-fab/cosalette/issues/22))

### Features

- :sparkles: initial commit
  ([03e1dfc](https://github.com/ff-fab/cosalette/commit/03e1dfc320118a308a7def5e26adb7056d8e6d3b))
- add [@app](https://github.com/app).command() decorator for command handler
  registration ([#23](https://github.com/ff-fab/cosalette/issues/23))
  ([9e09765](https://github.com/ff-fab/cosalette/commit/9e097654bd29638139f1295513ea2bf1013ad6d8))
- add CLI scaffolding with Typer ([#9](https://github.com/ff-fab/cosalette/issues/9))
  ([4a92f9a](https://github.com/ff-fab/cosalette/commit/4a92f9a76dabd6ffcd735b9f52beba0d6596137e))
- add cosalette.testing module (Phase 6)
  ([#11](https://github.com/ff-fab/cosalette/issues/11))
  ([fad9d74](https://github.com/ff-fab/cosalette/commit/fad9d74526e5c7e1e938276c27856ba95deb3f3b))
- add error publisher and health reporter (\_errors.py, \_health.py)
  ([#5](https://github.com/ff-fab/cosalette/issues/5))
  ([5c1599a](https://github.com/ff-fab/cosalette/commit/5c1599a326cce50bf5c157a650e4f8b61aabe8b1))
- add MQTT client port and adapters (\_mqtt.py)
  ([#3](https://github.com/ff-fab/cosalette/issues/3))
  ([7fc3efa](https://github.com/ff-fab/cosalette/commit/7fc3efaee14fc80912b9169429becc08b2feb560))
- add periodic heartbeat scheduling (opt-in, default 60s)
  ([#19](https://github.com/ff-fab/cosalette/issues/19))
  ([f41fca8](https://github.com/ff-fab/cosalette/commit/f41fca85ed5ac829165e705f3f8137dcc95d6ada))
- API ergonomics - app.run(), lifespan, injection
  ([#22](https://github.com/ff-fab/cosalette/issues/22))
  ([4fe51d6](https://github.com/ff-fab/cosalette/commit/4fe51d6bb14c9b3956ee7413718c8f8561babf08))
- migrate documentation from MkDocs to Zensical
  ([#21](https://github.com/ff-fab/cosalette/issues/21))
  ([674832c](https://github.com/ff-fab/cosalette/commit/674832c1d765a0dffdabf9d740b44e899155e157))
- optional MQTT params in [@app](https://github.com/app).command() + docs update
  ([#25](https://github.com/ff-fab/cosalette/issues/25))
  ([d1a4f25](https://github.com/ff-fab/cosalette/commit/d1a4f253dc2e6b366d99a527467b908accac2370))
- Phase 1 — Foundation modules (Settings, Clock, Logging)
  ([#2](https://github.com/ff-fab/cosalette/issues/2))
  ([3a7d4c0](https://github.com/ff-fab/cosalette/commit/3a7d4c0d8ca1edf8c91789ea4e3ad20efa697e22))
- Phase 4 — App orchestrator, DeviceContext, and TopicRouter
  ([#8](https://github.com/ff-fab/cosalette/issues/8))
  ([2ca3817](https://github.com/ff-fab/cosalette/commit/2ca38178ca4506c0ceff268f1dff9537a7ffcb95))
- pre-release polish — root devices, log rotation, reconnect backoff
  ([#27](https://github.com/ff-fab/cosalette/issues/27))
  ([fb641d6](https://github.com/ff-fab/cosalette/commit/fb641d6bd099c89bdec06a7a59cbe2bea05abb6d))
- public API, gate tasks, and integration tests (Phase 7)
  ([#12](https://github.com/ff-fab/cosalette/issues/12))
  ([47e75fb](https://github.com/ff-fab/cosalette/commit/47e75fb3ff1bdf159fd9ff55dc2eb0a6ca38549c))
- telemetry error deduplication and health integration
  ([#29](https://github.com/ff-fab/cosalette/issues/29))
  ([80a75bb](https://github.com/ff-fab/cosalette/commit/80a75bb342a46f7bd7d6cd00e592b128c74fd294))

### Bug Fixes

- improve error handling in device proxy and lifespan teardown
  ([#24](https://github.com/ff-fab/cosalette/issues/24))
  ([7c75c27](https://github.com/ff-fab/cosalette/commit/7c75c27c75a5d8488ffe4b2d2d02166176966d95))
- isolate make_settings from ambient environment variables
  ([#13](https://github.com/ff-fab/cosalette/issues/13))
  ([c2d0751](https://github.com/ff-fab/cosalette/commit/c2d0751420b110003d5774eb0466b73528c96c39))
