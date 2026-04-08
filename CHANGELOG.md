# Changelog

## [0.3.0](https://github.com/ff-fab/cosalette/compare/v0.2.1...v0.3.0) (2026-04-08)


### ⚠ BREAKING CHANGES

* add sub-topic routing for command handlers ([#131](https://github.com/ff-fab/cosalette/issues/131))

### Features

* add [@app](https://github.com/app).on_configure lifecycle hook and dict-name multi-device registration ([#124](https://github.com/ff-fab/cosalette/issues/124)) ([802b1a9](https://github.com/ff-fab/cosalette/commit/802b1a9d3597ae79a4a5c412288532d8fa767e36))
* add adapter auto-restart on health check failure ([#143](https://github.com/ff-fab/cosalette/issues/143)) ([9ecc509](https://github.com/ff-fab/cosalette/commit/9ecc5095b9d49df3a22233be028409c1b28bd31b))
* add brand identity assets and wire into docs site ([#148](https://github.com/ff-fab/cosalette/issues/148)) ([bacf04d](https://github.com/ff-fab/cosalette/commit/bacf04d360f88e10308f84acc06dc8bbaadd7425))
* add Command dataclass and per-device command queue ([#128](https://github.com/ff-fab/cosalette/issues/128)) ([a5a5515](https://github.com/ff-fab/cosalette/commit/a5a5515010d76c718383ec8812fb81f1776b57dd))
* add ctx.commands() async iterator for device loops ([#129](https://github.com/ff-fab/cosalette/issues/129)) ([154e4a3](https://github.com/ff-fab/cosalette/commit/154e4a3b15a8f115926044f0c7d5854e9a39c77f))
* add HealthCheckable protocol and adapter detection ([#136](https://github.com/ff-fab/cosalette/issues/136)) ([3b90561](https://github.com/ff-fab/cosalette/commit/3b905610427a8e1ca18c8ebebb1607442d2c977b))
* add hero banners, social preview, and brand asset scripts ([#150](https://github.com/ff-fab/cosalette/issues/150)) ([a07ee78](https://github.com/ff-fab/cosalette/commit/a07ee78a627ef2f3f70aff66d927dd0efae1097a))
* add lifespan-yielded injectable state (ADR-027) ([#134](https://github.com/ff-fab/cosalette/issues/134)) ([8b29d5c](https://github.com/ff-fab/cosalette/commit/8b29d5ca49770fef493c1f483759c0953ff93c44))
* add Open Graph meta tags, custom 404 page, and PyPI badge icon ([#152](https://github.com/ff-fab/cosalette/issues/152)) ([56309fe](https://github.com/ff-fab/cosalette/commit/56309fe9e5ee93010d22b4830c3e1e12b9554697))
* add periodic health check runner with availability toggling ([#137](https://github.com/ff-fab/cosalette/issues/137)) ([12a3e05](https://github.com/ff-fab/cosalette/commit/12a3e05e1088fee6930f3f3d667d11d40be8354c))
* add schedule= parameter and ctx.sleep_until() ([f9fc5d4](https://github.com/ff-fab/cosalette/commit/f9fc5d4476ecd3d943be889dab85fb33679033c8))
* add schedule= parameter and ctx.sleep_until() ([1938618](https://github.com/ff-fab/cosalette/commit/19386185a2d456e023147e1dfbac5938b2d79639))
* add sub-topic routing for command handlers ([#131](https://github.com/ff-fab/cosalette/issues/131)) ([af83892](https://github.com/ff-fab/cosalette/commit/af838927fcce537e81fc987607cdc05231a758fe))
* auto-publish registry snapshot and prune cancelled tasks ([#166](https://github.com/ff-fab/cosalette/issues/166)) ([93c3912](https://github.com/ff-fab/cosalette/commit/93c3912e484d841b0f853bc5303057bc42eb6e80))
* bind agent models and add docs-subagent ([#163](https://github.com/ff-fab/cosalette/issues/163)) ([7a59704](https://github.com/ff-fab/cosalette/commit/7a59704f80fabc8e10c0130a27d1d0bc6e9f1b3a))
* brand assets round 2 — logotype, favicon bg, theme logo ([#149](https://github.com/ff-fab/cosalette/issues/149)) ([2a2a5ca](https://github.com/ff-fab/cosalette/commit/2a2a5ca8162d5b41db55596c396a527aa656b87b))
* configurable retry/backoff on [@app](https://github.com/app).telemetry (ADR-024) ([#126](https://github.com/ff-fab/cosalette/issues/126)) ([fd23407](https://github.com/ff-fab/cosalette/commit/fd234079609bcd5e8c4f42f2e9617d93714c4fb5))
* Epic 8 — dynamic sub-entity availability (ADR-031) ([#158](https://github.com/ff-fab/cosalette/issues/158)) ([bd1fdb2](https://github.com/ff-fab/cosalette/commit/bd1fdb2b74df8d5d55cb154ecd92810d17c85c8e))
* hero responsive CSS and Mermaid brand color alignment ([#153](https://github.com/ff-fab/cosalette/issues/153)) ([e7364dd](https://github.com/ff-fab/cosalette/commit/e7364dd0e1ee6a077d51cc93d772b5fea66ca831))
* optimize agent orchestration system ([#160](https://github.com/ff-fab/cosalette/issues/160)) ([db9be04](https://github.com/ff-fab/cosalette/commit/db9be04721c47fa047ab5bdd16f8320e6cdb6e7e))
* schema-driven ADR creation with JSON validation and rendering ([#168](https://github.com/ff-fab/cosalette/issues/168)) ([7bdce06](https://github.com/ff-fab/cosalette/commit/7bdce06138d995a95871d5e5c6b69aa2394fa367))
* update brand identity brief and add docs hero illustration ([#151](https://github.com/ff-fab/cosalette/issues/151)) ([a8f88b7](https://github.com/ff-fab/cosalette/commit/a8f88b719638fcc6f5dfa5dce0272760f1ac4bcd))
* wire commands() queue dispatch and add integration tests ([#130](https://github.com/ff-fab/cosalette/issues/130)) ([46220bc](https://github.com/ff-fab/cosalette/commit/46220bc3cd8d5771bcda1366b885bb20ddad6b7c))


### Bug Fixes

* add continue-on-error to TestPyPI publish step ([1161b20](https://github.com/ff-fab/cosalette/commit/1161b20228e49b0283777151d95ed49365805d72))
* add shutdown-event race guard to enter_restartable_adapters ([#144](https://github.com/ff-fab/cosalette/issues/144)) ([4f8ad36](https://github.com/ff-fab/cosalette/commit/4f8ad365d7be537e636d88ed554ace2a4f4590ed))
* address review findings for schedule/cron feature ([c0d5e59](https://github.com/ff-fab/cosalette/commit/c0d5e599b4a77fe2a636421696f5b7ee22baf7e4))
* allow docs-only PRs to pass required CI checks ([#122](https://github.com/ff-fab/cosalette/issues/122)) ([d99b174](https://github.com/ff-fab/cosalette/commit/d99b174bd134f3fec24be6ab2f5281f7a32250ed))
* avoid cancelling shared coalescing group task on single-adapter restart ([#146](https://github.com/ff-fab/cosalette/issues/146)) ([c9915b2](https://github.com/ff-fab/cosalette/commit/c9915b2cdeb1064d13b8d6c9d8dd70b193ffcd92))
* collapse multiline SKILL.md descriptions and fix frontmatter typos ([00dd443](https://github.com/ff-fab/cosalette/commit/00dd4435c5da37e7344b2530fa937ed0c2e29489))
* collapse multiline SKILL.md descriptions and fix frontmatter typos ([c00a563](https://github.com/ff-fab/cosalette/commit/c00a5630a78740f10a57e050e8b330db0047ffba))
* improve commands() shutdown responsiveness for long timeouts ([#140](https://github.com/ff-fab/cosalette/issues/140)) ([860aa58](https://github.com/ff-fab/cosalette/commit/860aa58a35395e840a0913d13daa93a7915bd879))
* pre-0.3.0 framework review — strengthen settings validation and close test gaps ([#169](https://github.com/ff-fab/cosalette/issues/169)) ([06647e9](https://github.com/ff-fab/cosalette/commit/06647e992de5b12858dadbb90bdb2758ee4ab2b5))
* prevent silent docs build failures ([#141](https://github.com/ff-fab/cosalette/issues/141)) ([8deb27e](https://github.com/ff-fab/cosalette/commit/8deb27e92846c4eeacdfe3e151d0d236e3632e63))
* switch test report link from gistpreview to gist.githack.com ([d09ff3a](https://github.com/ff-fab/cosalette/commit/d09ff3a6c0f1edeb5e68965f10fdc88293866c3c))
* use jq --rawfile for gist update and update security policy ([ed08d85](https://github.com/ff-fab/cosalette/commit/ed08d858762276d9061c33929285afab8ba2e9dd))

## [0.2.1](https://github.com/ff-fab/cosalette/compare/v0.2.0...v0.2.1) (2026-03-13)


### Features

* add coverage and test count badges to README ([#115](https://github.com/ff-fab/cosalette/issues/115)) ([9f0ad6c](https://github.com/ff-fab/cosalette/commit/9f0ad6c39fb3cf68865a40b3eb2a5a105310746d))


### Bug Fixes

* include LICENSE in sdist and use non-interactive gist update ([#116](https://github.com/ff-fab/cosalette/issues/116)) ([030c03e](https://github.com/ff-fab/cosalette/commit/030c03e1d9a5d7bb9d51ae000c4f10255a0ba99d))
* make TestPyPI non-blocking for PyPI publish ([02eb2fa](https://github.com/ff-fab/cosalette/commit/02eb2fa08cd43cb4b73d68780b7059478683ab79))
* temporarily make TestPyPI non-blocking for v0.2.0 publish ([50b31c8](https://github.com/ff-fab/cosalette/commit/50b31c8e11ef244bc991b67e7315142efaaefb94))
* use PEP 639 SPDX license expression for Metadata 2.4 compatibility ([#113](https://github.com/ff-fab/cosalette/issues/113)) ([4d477a6](https://github.com/ff-fab/cosalette/commit/4d477a6f00ef99c0f3e079663be5b114fc6f6617))

## [0.2.1](https://github.com/ff-fab/cosalette/compare/v0.2.0...v0.2.1) (2026-03-13)


### Features

* add coverage and test count badges to README ([#115](https://github.com/ff-fab/cosalette/issues/115)) ([9f0ad6c](https://github.com/ff-fab/cosalette/commit/9f0ad6c39fb3cf68865a40b3eb2a5a105310746d))


### Bug Fixes

* make TestPyPI non-blocking for PyPI publish ([02eb2fa](https://github.com/ff-fab/cosalette/commit/02eb2fa08cd43cb4b73d68780b7059478683ab79))
* temporarily make TestPyPI non-blocking for v0.2.0 publish ([50b31c8](https://github.com/ff-fab/cosalette/commit/50b31c8e11ef244bc991b67e7315142efaaefb94))
* use PEP 639 SPDX license expression for Metadata 2.4 compatibility ([#113](https://github.com/ff-fab/cosalette/issues/113)) ([4d477a6](https://github.com/ff-fab/cosalette/commit/4d477a6f00ef99c0f3e079663be5b114fc6f6617))

## [0.2.0](https://github.com/ff-fab/cosalette/compare/v0.2.0...v0.2.0) (2026-03-13)


### ⚠ BREAKING CHANGES

* require Rust filters, drop Python fallback (ADR-022) ([#98](https://github.com/ff-fab/cosalette/issues/98))
* API ergonomics - app.run(), lifespan, injection ([#22](https://github.com/ff-fab/cosalette/issues/22))

### Features

* :sparkles: initial commit ([03e1dfc](https://github.com/ff-fab/cosalette/commit/03e1dfc320118a308a7def5e26adb7056d8e6d3b))
* abi3 wheels + Node.js 24 actions upgrade ([#103](https://github.com/ff-fab/cosalette/issues/103)) ([fdc9cfe](https://github.com/ff-fab/cosalette/commit/fdc9cfe9880a85dddb6643f334d279169c1625da))
* adapter factory settings injection ([#36](https://github.com/ff-fab/cosalette/issues/36)) ([12d14aa](https://github.com/ff-fab/cosalette/commit/12d14aa2a0d671f0c684c87a3f180451b18cc599))
* add --show-devices and --show-devices-json CLI flags ([#92](https://github.com/ff-fab/cosalette/issues/92)) ([1378500](https://github.com/ff-fab/cosalette/commit/1378500cdd9c0f33e7632d21c0b9e28f74037fe6))
* add [@app](https://github.com/app).command() decorator for command handler registration ([#23](https://github.com/ff-fab/cosalette/issues/23)) ([9e09765](https://github.com/ff-fab/cosalette/commit/9e097654bd29638139f1295513ea2bf1013ad6d8))
* add adapters= dict parameter to App constructor ([#53](https://github.com/ff-fab/cosalette/issues/53)) ([a9e54af](https://github.com/ff-fab/cosalette/commit/a9e54af08e345e16f6c113f6b770e3b34892da82))
* add benchmark suite for hot paths ([#93](https://github.com/ff-fab/cosalette/issues/93)) ([72bdccd](https://github.com/ff-fab/cosalette/commit/72bdccd6d407541716731caa7c06ae17ce60b069))
* add CLI scaffolding with Typer ([#9](https://github.com/ff-fab/cosalette/issues/9)) ([4a92f9a](https://github.com/ff-fab/cosalette/commit/4a92f9a76dabd6ffcd735b9f52beba0d6596137e))
* add clock-controlled sleep to ClockPort protocol ([#85](https://github.com/ff-fab/cosalette/issues/85)) ([021564b](https://github.com/ff-fab/cosalette/commit/021564bb0363691dae50bd98a4b1c83ebcaaee63))
* add cognitive complexity gate and refactor all violations below threshold ([9c2d88e](https://github.com/ff-fab/cosalette/commit/9c2d88ec67403268db5358882858de9add8ca379)), closes [#71](https://github.com/ff-fab/cosalette/issues/71)
* add cosalette.testing module (Phase 6) ([#11](https://github.com/ff-fab/cosalette/issues/11)) ([fad9d74](https://github.com/ff-fab/cosalette/commit/fad9d74526e5c7e1e938276c27856ba95deb3f3b))
* add enabled= parameter for conditional device registration ([#52](https://github.com/ff-fab/cosalette/issues/52)) ([47389f4](https://github.com/ff-fab/cosalette/commit/47389f476007c39f9cd43aabfa3146c5a2bfad4b))
* add error publisher and health reporter (_errors.py, _health.py) ([#5](https://github.com/ff-fab/cosalette/issues/5)) ([5c1599a](https://github.com/ff-fab/cosalette/commit/5c1599a326cce50bf5c157a650e4f8b61aabe8b1))
* add imperative add_device/add_telemetry/add_command methods ([#51](https://github.com/ff-fab/cosalette/issues/51)) ([180e75c](https://github.com/ff-fab/cosalette/commit/180e75cfeb449d35a74c13329cecc6e3dabfe848))
* add init= callback for per-device state injection ([#46](https://github.com/ff-fab/cosalette/issues/46)) ([3192267](https://github.com/ff-fab/cosalette/commit/3192267c30f56cf55a9b0dc610d20edfaaf3c987))
* add IntervalSpec type for deferred telemetry interval resolution ([#68](https://github.com/ff-fab/cosalette/issues/68)) ([8bf5733](https://github.com/ff-fab/cosalette/commit/8bf5733aa9497b8f07c5e35d942ace1984bd65d7))
* add MQTT client port and adapters (_mqtt.py) ([#3](https://github.com/ff-fab/cosalette/issues/3)) ([7fc3efa](https://github.com/ff-fab/cosalette/commit/7fc3efaee14fc80912b9169429becc08b2feb560))
* add MQTT integration tests with testcontainers (COS-0ky) ([#90](https://github.com/ff-fab/cosalette/issues/90)) ([edc45b4](https://github.com/ff-fab/cosalette/commit/edc45b457011c8ee8bdc7df0baa72ee7a67961a6))
* add periodic heartbeat scheduling (opt-in, default 60s) ([#19](https://github.com/ff-fab/cosalette/issues/19)) ([f41fca8](https://github.com/ff-fab/cosalette/commit/f41fca85ed5ac829165e705f3f8137dcc95d6ada))
* add persistence system with Store protocol, DeviceStore, and save policies ([#47](https://github.com/ff-fab/cosalette/issues/47)) ([c5528e3](https://github.com/ff-fab/cosalette/commit/c5528e3ea6231315ba8e6772955c2ffe5bbecf76))
* add property-based tests with Hypothesis (COS-rmy) ([#88](https://github.com/ff-fab/cosalette/issues/88)) ([f285c1b](https://github.com/ff-fab/cosalette/commit/f285c1be9714a6e20de9c8ac3f60629b2b0b8605))
* add publish strategies for telemetry devices ([#38](https://github.com/ff-fab/cosalette/issues/38)) ([751b35e](https://github.com/ff-fab/cosalette/commit/751b35eaacb4e6957dcd04470f442feb30ce0bc2))
* add registry introspection module (_introspect.py) ([#91](https://github.com/ff-fab/cosalette/issues/91)) ([4fa25ef](https://github.com/ff-fab/cosalette/commit/4fa25ef04edf66515d61f059876471090c27d334))
* add telemetry coalescing groups ([#62](https://github.com/ff-fab/cosalette/issues/62)) ([d140658](https://github.com/ff-fab/cosalette/commit/d1406585f9f33a647d7dde1dc0b15ac2930c6e83))
* adopt orjson as hard dependency (COS-gjp) ([#87](https://github.com/ff-fab/cosalette/issues/87)) ([4f123ee](https://github.com/ff-fab/cosalette/commit/4f123ee450b7f5633adb5afc9100edd540f13cdb))
* API ergonomics - app.run(), lifespan, injection ([#22](https://github.com/ff-fab/cosalette/issues/22)) ([4fe51d6](https://github.com/ff-fab/cosalette/commit/4fe51d6bb14c9b3956ee7413718c8f8561babf08))
* auto-manage adapter lifecycle via async context manager protocol ([#49](https://github.com/ff-fab/cosalette/issues/49)) ([c1b0843](https://github.com/ff-fab/cosalette/commit/c1b0843da74ec8c3592e9d0003957aba7eec4e5c))
* CI wheel matrix + single maturin package ([#102](https://github.com/ff-fab/cosalette/issues/102)) ([21fa324](https://github.com/ff-fab/cosalette/commit/21fa324d544f35c404fbe9a7f60666ccded7ffec))
* click-to-zoom overlay for Mermaid diagrams ([#107](https://github.com/ff-fab/cosalette/issues/107)) ([398249d](https://github.com/ff-fab/cosalette/commit/398249dffcbff1fd4b808a2631e548ebf0848ef9))
* eagerly instantiate settings in App.__init__ ([#44](https://github.com/ff-fab/cosalette/issues/44)) ([59d1b6f](https://github.com/ff-fab/cosalette/commit/59d1b6fea8a82956b810498db51b706869fcefd0))
* export composite strategy types and add strategies re-export module ([#84](https://github.com/ff-fab/cosalette/issues/84)) ([5ac8946](https://github.com/ff-fab/cosalette/commit/5ac894692e121b90387dd2a1c6cfe073f7ca47a7))
* MedianFilter + OneEuroFilter in Rust with dual-backend tests ([#97](https://github.com/ff-fab/cosalette/issues/97)) ([dd4c42b](https://github.com/ff-fab/cosalette/commit/dd4c42bef745a9eb6c231964e5d0310befdf9297))
* migrate documentation from MkDocs to Zensical ([#21](https://github.com/ff-fab/cosalette/issues/21)) ([674832c](https://github.com/ff-fab/cosalette/commit/674832c1d765a0dffdabf9d740b44e899155e157))
* optional MQTT params in [@app](https://github.com/app).command() + docs update ([#25](https://github.com/ff-fab/cosalette/issues/25)) ([d1a4f25](https://github.com/ff-fab/cosalette/commit/d1a4f253dc2e6b366d99a527467b908accac2370))
* Phase 1 — Foundation modules (Settings, Clock, Logging) ([#2](https://github.com/ff-fab/cosalette/issues/2)) ([3a7d4c0](https://github.com/ff-fab/cosalette/commit/3a7d4c0d8ca1edf8c91789ea4e3ad20efa697e22))
* Phase 4 — App orchestrator, DeviceContext, and TopicRouter ([#8](https://github.com/ff-fab/cosalette/issues/8)) ([2ca3817](https://github.com/ff-fab/cosalette/commit/2ca38178ca4506c0ceff268f1dff9537a7ffcb95))
* pre-release polish — root devices, log rotation, reconnect backoff ([#27](https://github.com/ff-fab/cosalette/issues/27)) ([fb641d6](https://github.com/ff-fab/cosalette/commit/fb641d6bd099c89bdec06a7a59cbe2bea05abb6d))
* Pt1Filter in Rust with dual-backend test parametrization ([#95](https://github.com/ff-fab/cosalette/issues/95)) ([1ceabed](https://github.com/ff-fab/cosalette/commit/1ceabed8db5854a9673e868a2b102e37dec0c14c))
* public API, gate tasks, and integration tests (Phase 7) ([#12](https://github.com/ff-fab/cosalette/issues/12)) ([47e75fb](https://github.com/ff-fab/cosalette/commit/47e75fb3ff1bdf159fd9ff55dc2eb0a6ca38549c))
* recursive leaf-level thresholds and strategy documentation ([#40](https://github.com/ff-fab/cosalette/issues/40)) ([3c7f9cd](https://github.com/ff-fab/cosalette/commit/3c7f9cdcbc2804e2278df1f29f90b4fcaa161fba))
* require Rust filters, drop Python fallback (ADR-022) ([#98](https://github.com/ff-fab/cosalette/issues/98)) ([21bd4b5](https://github.com/ff-fab/cosalette/commit/21bd4b52f120a00cf8ef289d36a845f80d784210))
* Rust project scaffold for cosalette-filters-rs (pyo3/maturin) ([#94](https://github.com/ff-fab/cosalette/issues/94)) ([13f13eb](https://github.com/ff-fab/cosalette/commit/13f13eba95795025edde21fdfc50f663ac31d11b))
* scoped name uniqueness for shared telemetry+command topics ([#66](https://github.com/ff-fab/cosalette/issues/66)) ([a489a3e](https://github.com/ff-fab/cosalette/commit/a489a3e494c560c6b983a513156fb2857ff8392e))
* settings-aware adapter constructors with unified DI pipeline ([#50](https://github.com/ff-fab/cosalette/issues/50)) ([65436ba](https://github.com/ff-fab/cosalette/commit/65436ba7354420c9273a40fd4b095e2f66f7f4d6))
* signal filters utility library (Pt1Filter, MedianFilter, OneEuroFilter) ([#41](https://github.com/ff-fab/cosalette/issues/41)) ([15c3ace](https://github.com/ff-fab/cosalette/commit/15c3acef11bec302b61367bea57bd302bb80b389))
* telemetry error deduplication and health integration ([#29](https://github.com/ff-fab/cosalette/issues/29)) ([80a75bb](https://github.com/ff-fab/cosalette/commit/80a75bb342a46f7bd7d6cd00e592b128c74fd294))


### Bug Fixes

* add skip-existing for TestPyPI publish ([5a2acd0](https://github.com/ff-fab/cosalette/commit/5a2acd0025b3e6fe41ea066a6d9a31a3057fee8f))
* add workflow_dispatch trigger and release-as 0.2.0 override ([6eabd4b](https://github.com/ff-fab/cosalette/commit/6eabd4b7c4c9fa9bb9cd1faf8afb0472545a1487))
* bypass upstream beads install script WSL URL corruption ([8d2d239](https://github.com/ff-fab/cosalette/commit/8d2d239b08da01a7bb0c7e8d895508949472c528))
* bypass upstream beads install script WSL URL corruption ([d4d832f](https://github.com/ff-fab/cosalette/commit/d4d832f1b45bceabe7e6cd80937e5d857eafb64e))
* cancel adapter entry on shutdown signal ([#56](https://github.com/ff-fab/cosalette/issues/56)) ([4606b21](https://github.com/ff-fab/cosalette/commit/4606b21ed0579a673bfe2c25b104a90db405155a))
* drop file: prefix from syft command to fix glob expansion ([#59](https://github.com/ff-fab/cosalette/issues/59)) ([caa03e6](https://github.com/ff-fab/cosalette/commit/caa03e61e3151820e232aae8bd9d2e54cbde8bcd))
* improve error handling in device proxy and lifespan teardown ([#24](https://github.com/ff-fab/cosalette/issues/24)) ([7c75c27](https://github.com/ff-fab/cosalette/commit/7c75c27c75a5d8488ffe4b2d2d02166176966d95))
* install signal handlers before adapter lifecycle entry ([#55](https://github.com/ff-fab/cosalette/issues/55)) ([29d5351](https://github.com/ff-fab/cosalette/commit/29d535132d2a18dfd291707268d1587631eed131))
* isolate make_settings from ambient environment variables ([#13](https://github.com/ff-fab/cosalette/issues/13)) ([c2d0751](https://github.com/ff-fab/cosalette/commit/c2d0751420b110003d5774eb0466b73528c96c39))
* reject NaN/Inf in filter constructors and update() ([#100](https://github.com/ff-fab/cosalette/issues/100)) ([c3f10ac](https://github.com/ff-fab/cosalette/commit/c3f10ac1e7a96d228b1e62960f5a688da99a6cec))
* replace deprecated macos-13 runner with macos-14 cross-compile ([#108](https://github.com/ff-fab/cosalette/issues/108)) ([239b999](https://github.com/ff-fab/cosalette/commit/239b999a219f850fcca349a5f6485ab0e928610f))
* set #[pyclass(module)] on all pyo3 filter classes ([#99](https://github.com/ff-fab/cosalette/issues/99)) ([becc5dd](https://github.com/ff-fab/cosalette/commit/becc5ddf7bcff325d9f069a475b4fb9fb0d233fd))

## [0.2.0](https://github.com/ff-fab/cosalette/compare/v0.2.0...v0.2.0) (2026-03-13)


### ⚠ BREAKING CHANGES

* require Rust filters, drop Python fallback (ADR-022) ([#98](https://github.com/ff-fab/cosalette/issues/98))
* API ergonomics - app.run(), lifespan, injection ([#22](https://github.com/ff-fab/cosalette/issues/22))

### Features

* :sparkles: initial commit ([03e1dfc](https://github.com/ff-fab/cosalette/commit/03e1dfc320118a308a7def5e26adb7056d8e6d3b))
* abi3 wheels + Node.js 24 actions upgrade ([#103](https://github.com/ff-fab/cosalette/issues/103)) ([fdc9cfe](https://github.com/ff-fab/cosalette/commit/fdc9cfe9880a85dddb6643f334d279169c1625da))
* adapter factory settings injection ([#36](https://github.com/ff-fab/cosalette/issues/36)) ([12d14aa](https://github.com/ff-fab/cosalette/commit/12d14aa2a0d671f0c684c87a3f180451b18cc599))
* add --show-devices and --show-devices-json CLI flags ([#92](https://github.com/ff-fab/cosalette/issues/92)) ([1378500](https://github.com/ff-fab/cosalette/commit/1378500cdd9c0f33e7632d21c0b9e28f74037fe6))
* add [@app](https://github.com/app).command() decorator for command handler registration ([#23](https://github.com/ff-fab/cosalette/issues/23)) ([9e09765](https://github.com/ff-fab/cosalette/commit/9e097654bd29638139f1295513ea2bf1013ad6d8))
* add adapters= dict parameter to App constructor ([#53](https://github.com/ff-fab/cosalette/issues/53)) ([a9e54af](https://github.com/ff-fab/cosalette/commit/a9e54af08e345e16f6c113f6b770e3b34892da82))
* add benchmark suite for hot paths ([#93](https://github.com/ff-fab/cosalette/issues/93)) ([72bdccd](https://github.com/ff-fab/cosalette/commit/72bdccd6d407541716731caa7c06ae17ce60b069))
* add CLI scaffolding with Typer ([#9](https://github.com/ff-fab/cosalette/issues/9)) ([4a92f9a](https://github.com/ff-fab/cosalette/commit/4a92f9a76dabd6ffcd735b9f52beba0d6596137e))
* add clock-controlled sleep to ClockPort protocol ([#85](https://github.com/ff-fab/cosalette/issues/85)) ([021564b](https://github.com/ff-fab/cosalette/commit/021564bb0363691dae50bd98a4b1c83ebcaaee63))
* add cognitive complexity gate and refactor all violations below threshold ([9c2d88e](https://github.com/ff-fab/cosalette/commit/9c2d88ec67403268db5358882858de9add8ca379)), closes [#71](https://github.com/ff-fab/cosalette/issues/71)
* add cosalette.testing module (Phase 6) ([#11](https://github.com/ff-fab/cosalette/issues/11)) ([fad9d74](https://github.com/ff-fab/cosalette/commit/fad9d74526e5c7e1e938276c27856ba95deb3f3b))
* add enabled= parameter for conditional device registration ([#52](https://github.com/ff-fab/cosalette/issues/52)) ([47389f4](https://github.com/ff-fab/cosalette/commit/47389f476007c39f9cd43aabfa3146c5a2bfad4b))
* add error publisher and health reporter (_errors.py, _health.py) ([#5](https://github.com/ff-fab/cosalette/issues/5)) ([5c1599a](https://github.com/ff-fab/cosalette/commit/5c1599a326cce50bf5c157a650e4f8b61aabe8b1))
* add imperative add_device/add_telemetry/add_command methods ([#51](https://github.com/ff-fab/cosalette/issues/51)) ([180e75c](https://github.com/ff-fab/cosalette/commit/180e75cfeb449d35a74c13329cecc6e3dabfe848))
* add init= callback for per-device state injection ([#46](https://github.com/ff-fab/cosalette/issues/46)) ([3192267](https://github.com/ff-fab/cosalette/commit/3192267c30f56cf55a9b0dc610d20edfaaf3c987))
* add IntervalSpec type for deferred telemetry interval resolution ([#68](https://github.com/ff-fab/cosalette/issues/68)) ([8bf5733](https://github.com/ff-fab/cosalette/commit/8bf5733aa9497b8f07c5e35d942ace1984bd65d7))
* add MQTT client port and adapters (_mqtt.py) ([#3](https://github.com/ff-fab/cosalette/issues/3)) ([7fc3efa](https://github.com/ff-fab/cosalette/commit/7fc3efaee14fc80912b9169429becc08b2feb560))
* add MQTT integration tests with testcontainers (COS-0ky) ([#90](https://github.com/ff-fab/cosalette/issues/90)) ([edc45b4](https://github.com/ff-fab/cosalette/commit/edc45b457011c8ee8bdc7df0baa72ee7a67961a6))
* add periodic heartbeat scheduling (opt-in, default 60s) ([#19](https://github.com/ff-fab/cosalette/issues/19)) ([f41fca8](https://github.com/ff-fab/cosalette/commit/f41fca85ed5ac829165e705f3f8137dcc95d6ada))
* add persistence system with Store protocol, DeviceStore, and save policies ([#47](https://github.com/ff-fab/cosalette/issues/47)) ([c5528e3](https://github.com/ff-fab/cosalette/commit/c5528e3ea6231315ba8e6772955c2ffe5bbecf76))
* add property-based tests with Hypothesis (COS-rmy) ([#88](https://github.com/ff-fab/cosalette/issues/88)) ([f285c1b](https://github.com/ff-fab/cosalette/commit/f285c1be9714a6e20de9c8ac3f60629b2b0b8605))
* add publish strategies for telemetry devices ([#38](https://github.com/ff-fab/cosalette/issues/38)) ([751b35e](https://github.com/ff-fab/cosalette/commit/751b35eaacb4e6957dcd04470f442feb30ce0bc2))
* add registry introspection module (_introspect.py) ([#91](https://github.com/ff-fab/cosalette/issues/91)) ([4fa25ef](https://github.com/ff-fab/cosalette/commit/4fa25ef04edf66515d61f059876471090c27d334))
* add telemetry coalescing groups ([#62](https://github.com/ff-fab/cosalette/issues/62)) ([d140658](https://github.com/ff-fab/cosalette/commit/d1406585f9f33a647d7dde1dc0b15ac2930c6e83))
* adopt orjson as hard dependency (COS-gjp) ([#87](https://github.com/ff-fab/cosalette/issues/87)) ([4f123ee](https://github.com/ff-fab/cosalette/commit/4f123ee450b7f5633adb5afc9100edd540f13cdb))
* API ergonomics - app.run(), lifespan, injection ([#22](https://github.com/ff-fab/cosalette/issues/22)) ([4fe51d6](https://github.com/ff-fab/cosalette/commit/4fe51d6bb14c9b3956ee7413718c8f8561babf08))
* auto-manage adapter lifecycle via async context manager protocol ([#49](https://github.com/ff-fab/cosalette/issues/49)) ([c1b0843](https://github.com/ff-fab/cosalette/commit/c1b0843da74ec8c3592e9d0003957aba7eec4e5c))
* CI wheel matrix + single maturin package ([#102](https://github.com/ff-fab/cosalette/issues/102)) ([21fa324](https://github.com/ff-fab/cosalette/commit/21fa324d544f35c404fbe9a7f60666ccded7ffec))
* click-to-zoom overlay for Mermaid diagrams ([#107](https://github.com/ff-fab/cosalette/issues/107)) ([398249d](https://github.com/ff-fab/cosalette/commit/398249dffcbff1fd4b808a2631e548ebf0848ef9))
* eagerly instantiate settings in App.__init__ ([#44](https://github.com/ff-fab/cosalette/issues/44)) ([59d1b6f](https://github.com/ff-fab/cosalette/commit/59d1b6fea8a82956b810498db51b706869fcefd0))
* export composite strategy types and add strategies re-export module ([#84](https://github.com/ff-fab/cosalette/issues/84)) ([5ac8946](https://github.com/ff-fab/cosalette/commit/5ac894692e121b90387dd2a1c6cfe073f7ca47a7))
* MedianFilter + OneEuroFilter in Rust with dual-backend tests ([#97](https://github.com/ff-fab/cosalette/issues/97)) ([dd4c42b](https://github.com/ff-fab/cosalette/commit/dd4c42bef745a9eb6c231964e5d0310befdf9297))
* migrate documentation from MkDocs to Zensical ([#21](https://github.com/ff-fab/cosalette/issues/21)) ([674832c](https://github.com/ff-fab/cosalette/commit/674832c1d765a0dffdabf9d740b44e899155e157))
* optional MQTT params in [@app](https://github.com/app).command() + docs update ([#25](https://github.com/ff-fab/cosalette/issues/25)) ([d1a4f25](https://github.com/ff-fab/cosalette/commit/d1a4f253dc2e6b366d99a527467b908accac2370))
* Phase 1 — Foundation modules (Settings, Clock, Logging) ([#2](https://github.com/ff-fab/cosalette/issues/2)) ([3a7d4c0](https://github.com/ff-fab/cosalette/commit/3a7d4c0d8ca1edf8c91789ea4e3ad20efa697e22))
* Phase 4 — App orchestrator, DeviceContext, and TopicRouter ([#8](https://github.com/ff-fab/cosalette/issues/8)) ([2ca3817](https://github.com/ff-fab/cosalette/commit/2ca38178ca4506c0ceff268f1dff9537a7ffcb95))
* pre-release polish — root devices, log rotation, reconnect backoff ([#27](https://github.com/ff-fab/cosalette/issues/27)) ([fb641d6](https://github.com/ff-fab/cosalette/commit/fb641d6bd099c89bdec06a7a59cbe2bea05abb6d))
* Pt1Filter in Rust with dual-backend test parametrization ([#95](https://github.com/ff-fab/cosalette/issues/95)) ([1ceabed](https://github.com/ff-fab/cosalette/commit/1ceabed8db5854a9673e868a2b102e37dec0c14c))
* public API, gate tasks, and integration tests (Phase 7) ([#12](https://github.com/ff-fab/cosalette/issues/12)) ([47e75fb](https://github.com/ff-fab/cosalette/commit/47e75fb3ff1bdf159fd9ff55dc2eb0a6ca38549c))
* recursive leaf-level thresholds and strategy documentation ([#40](https://github.com/ff-fab/cosalette/issues/40)) ([3c7f9cd](https://github.com/ff-fab/cosalette/commit/3c7f9cdcbc2804e2278df1f29f90b4fcaa161fba))
* require Rust filters, drop Python fallback (ADR-022) ([#98](https://github.com/ff-fab/cosalette/issues/98)) ([21bd4b5](https://github.com/ff-fab/cosalette/commit/21bd4b52f120a00cf8ef289d36a845f80d784210))
* Rust project scaffold for cosalette-filters-rs (pyo3/maturin) ([#94](https://github.com/ff-fab/cosalette/issues/94)) ([13f13eb](https://github.com/ff-fab/cosalette/commit/13f13eba95795025edde21fdfc50f663ac31d11b))
* scoped name uniqueness for shared telemetry+command topics ([#66](https://github.com/ff-fab/cosalette/issues/66)) ([a489a3e](https://github.com/ff-fab/cosalette/commit/a489a3e494c560c6b983a513156fb2857ff8392e))
* settings-aware adapter constructors with unified DI pipeline ([#50](https://github.com/ff-fab/cosalette/issues/50)) ([65436ba](https://github.com/ff-fab/cosalette/commit/65436ba7354420c9273a40fd4b095e2f66f7f4d6))
* signal filters utility library (Pt1Filter, MedianFilter, OneEuroFilter) ([#41](https://github.com/ff-fab/cosalette/issues/41)) ([15c3ace](https://github.com/ff-fab/cosalette/commit/15c3acef11bec302b61367bea57bd302bb80b389))
* telemetry error deduplication and health integration ([#29](https://github.com/ff-fab/cosalette/issues/29)) ([80a75bb](https://github.com/ff-fab/cosalette/commit/80a75bb342a46f7bd7d6cd00e592b128c74fd294))


### Bug Fixes

* bypass upstream beads install script WSL URL corruption ([8d2d239](https://github.com/ff-fab/cosalette/commit/8d2d239b08da01a7bb0c7e8d895508949472c528))
* bypass upstream beads install script WSL URL corruption ([d4d832f](https://github.com/ff-fab/cosalette/commit/d4d832f1b45bceabe7e6cd80937e5d857eafb64e))
* cancel adapter entry on shutdown signal ([#56](https://github.com/ff-fab/cosalette/issues/56)) ([4606b21](https://github.com/ff-fab/cosalette/commit/4606b21ed0579a673bfe2c25b104a90db405155a))
* drop file: prefix from syft command to fix glob expansion ([#59](https://github.com/ff-fab/cosalette/issues/59)) ([caa03e6](https://github.com/ff-fab/cosalette/commit/caa03e61e3151820e232aae8bd9d2e54cbde8bcd))
* improve error handling in device proxy and lifespan teardown ([#24](https://github.com/ff-fab/cosalette/issues/24)) ([7c75c27](https://github.com/ff-fab/cosalette/commit/7c75c27c75a5d8488ffe4b2d2d02166176966d95))
* install signal handlers before adapter lifecycle entry ([#55](https://github.com/ff-fab/cosalette/issues/55)) ([29d5351](https://github.com/ff-fab/cosalette/commit/29d535132d2a18dfd291707268d1587631eed131))
* isolate make_settings from ambient environment variables ([#13](https://github.com/ff-fab/cosalette/issues/13)) ([c2d0751](https://github.com/ff-fab/cosalette/commit/c2d0751420b110003d5774eb0466b73528c96c39))
* reject NaN/Inf in filter constructors and update() ([#100](https://github.com/ff-fab/cosalette/issues/100)) ([c3f10ac](https://github.com/ff-fab/cosalette/commit/c3f10ac1e7a96d228b1e62960f5a688da99a6cec))
* replace deprecated macos-13 runner with macos-14 cross-compile ([#108](https://github.com/ff-fab/cosalette/issues/108)) ([239b999](https://github.com/ff-fab/cosalette/commit/239b999a219f850fcca349a5f6485ab0e928610f))
* set #[pyclass(module)] on all pyo3 filter classes ([#99](https://github.com/ff-fab/cosalette/issues/99)) ([becc5dd](https://github.com/ff-fab/cosalette/commit/becc5ddf7bcff325d9f069a475b4fb9fb0d233fd))

## [0.2.0](https://github.com/ff-fab/cosalette/compare/v0.2.0...v0.2.0) (2026-03-13)


### ⚠ BREAKING CHANGES

* require Rust filters, drop Python fallback (ADR-022) ([#98](https://github.com/ff-fab/cosalette/issues/98))
* API ergonomics - app.run(), lifespan, injection ([#22](https://github.com/ff-fab/cosalette/issues/22))

### Features

* :sparkles: initial commit ([03e1dfc](https://github.com/ff-fab/cosalette/commit/03e1dfc320118a308a7def5e26adb7056d8e6d3b))
* abi3 wheels + Node.js 24 actions upgrade ([#103](https://github.com/ff-fab/cosalette/issues/103)) ([fdc9cfe](https://github.com/ff-fab/cosalette/commit/fdc9cfe9880a85dddb6643f334d279169c1625da))
* adapter factory settings injection ([#36](https://github.com/ff-fab/cosalette/issues/36)) ([12d14aa](https://github.com/ff-fab/cosalette/commit/12d14aa2a0d671f0c684c87a3f180451b18cc599))
* add --show-devices and --show-devices-json CLI flags ([#92](https://github.com/ff-fab/cosalette/issues/92)) ([1378500](https://github.com/ff-fab/cosalette/commit/1378500cdd9c0f33e7632d21c0b9e28f74037fe6))
* add [@app](https://github.com/app).command() decorator for command handler registration ([#23](https://github.com/ff-fab/cosalette/issues/23)) ([9e09765](https://github.com/ff-fab/cosalette/commit/9e097654bd29638139f1295513ea2bf1013ad6d8))
* add adapters= dict parameter to App constructor ([#53](https://github.com/ff-fab/cosalette/issues/53)) ([a9e54af](https://github.com/ff-fab/cosalette/commit/a9e54af08e345e16f6c113f6b770e3b34892da82))
* add benchmark suite for hot paths ([#93](https://github.com/ff-fab/cosalette/issues/93)) ([72bdccd](https://github.com/ff-fab/cosalette/commit/72bdccd6d407541716731caa7c06ae17ce60b069))
* add CLI scaffolding with Typer ([#9](https://github.com/ff-fab/cosalette/issues/9)) ([4a92f9a](https://github.com/ff-fab/cosalette/commit/4a92f9a76dabd6ffcd735b9f52beba0d6596137e))
* add clock-controlled sleep to ClockPort protocol ([#85](https://github.com/ff-fab/cosalette/issues/85)) ([021564b](https://github.com/ff-fab/cosalette/commit/021564bb0363691dae50bd98a4b1c83ebcaaee63))
* add cognitive complexity gate and refactor all violations below threshold ([9c2d88e](https://github.com/ff-fab/cosalette/commit/9c2d88ec67403268db5358882858de9add8ca379)), closes [#71](https://github.com/ff-fab/cosalette/issues/71)
* add cosalette.testing module (Phase 6) ([#11](https://github.com/ff-fab/cosalette/issues/11)) ([fad9d74](https://github.com/ff-fab/cosalette/commit/fad9d74526e5c7e1e938276c27856ba95deb3f3b))
* add enabled= parameter for conditional device registration ([#52](https://github.com/ff-fab/cosalette/issues/52)) ([47389f4](https://github.com/ff-fab/cosalette/commit/47389f476007c39f9cd43aabfa3146c5a2bfad4b))
* add error publisher and health reporter (_errors.py, _health.py) ([#5](https://github.com/ff-fab/cosalette/issues/5)) ([5c1599a](https://github.com/ff-fab/cosalette/commit/5c1599a326cce50bf5c157a650e4f8b61aabe8b1))
* add imperative add_device/add_telemetry/add_command methods ([#51](https://github.com/ff-fab/cosalette/issues/51)) ([180e75c](https://github.com/ff-fab/cosalette/commit/180e75cfeb449d35a74c13329cecc6e3dabfe848))
* add init= callback for per-device state injection ([#46](https://github.com/ff-fab/cosalette/issues/46)) ([3192267](https://github.com/ff-fab/cosalette/commit/3192267c30f56cf55a9b0dc610d20edfaaf3c987))
* add IntervalSpec type for deferred telemetry interval resolution ([#68](https://github.com/ff-fab/cosalette/issues/68)) ([8bf5733](https://github.com/ff-fab/cosalette/commit/8bf5733aa9497b8f07c5e35d942ace1984bd65d7))
* add MQTT client port and adapters (_mqtt.py) ([#3](https://github.com/ff-fab/cosalette/issues/3)) ([7fc3efa](https://github.com/ff-fab/cosalette/commit/7fc3efaee14fc80912b9169429becc08b2feb560))
* add MQTT integration tests with testcontainers (COS-0ky) ([#90](https://github.com/ff-fab/cosalette/issues/90)) ([edc45b4](https://github.com/ff-fab/cosalette/commit/edc45b457011c8ee8bdc7df0baa72ee7a67961a6))
* add periodic heartbeat scheduling (opt-in, default 60s) ([#19](https://github.com/ff-fab/cosalette/issues/19)) ([f41fca8](https://github.com/ff-fab/cosalette/commit/f41fca85ed5ac829165e705f3f8137dcc95d6ada))
* add persistence system with Store protocol, DeviceStore, and save policies ([#47](https://github.com/ff-fab/cosalette/issues/47)) ([c5528e3](https://github.com/ff-fab/cosalette/commit/c5528e3ea6231315ba8e6772955c2ffe5bbecf76))
* add property-based tests with Hypothesis (COS-rmy) ([#88](https://github.com/ff-fab/cosalette/issues/88)) ([f285c1b](https://github.com/ff-fab/cosalette/commit/f285c1be9714a6e20de9c8ac3f60629b2b0b8605))
* add publish strategies for telemetry devices ([#38](https://github.com/ff-fab/cosalette/issues/38)) ([751b35e](https://github.com/ff-fab/cosalette/commit/751b35eaacb4e6957dcd04470f442feb30ce0bc2))
* add registry introspection module (_introspect.py) ([#91](https://github.com/ff-fab/cosalette/issues/91)) ([4fa25ef](https://github.com/ff-fab/cosalette/commit/4fa25ef04edf66515d61f059876471090c27d334))
* add telemetry coalescing groups ([#62](https://github.com/ff-fab/cosalette/issues/62)) ([d140658](https://github.com/ff-fab/cosalette/commit/d1406585f9f33a647d7dde1dc0b15ac2930c6e83))
* adopt orjson as hard dependency (COS-gjp) ([#87](https://github.com/ff-fab/cosalette/issues/87)) ([4f123ee](https://github.com/ff-fab/cosalette/commit/4f123ee450b7f5633adb5afc9100edd540f13cdb))
* API ergonomics - app.run(), lifespan, injection ([#22](https://github.com/ff-fab/cosalette/issues/22)) ([4fe51d6](https://github.com/ff-fab/cosalette/commit/4fe51d6bb14c9b3956ee7413718c8f8561babf08))
* auto-manage adapter lifecycle via async context manager protocol ([#49](https://github.com/ff-fab/cosalette/issues/49)) ([c1b0843](https://github.com/ff-fab/cosalette/commit/c1b0843da74ec8c3592e9d0003957aba7eec4e5c))
* CI wheel matrix + single maturin package ([#102](https://github.com/ff-fab/cosalette/issues/102)) ([21fa324](https://github.com/ff-fab/cosalette/commit/21fa324d544f35c404fbe9a7f60666ccded7ffec))
* click-to-zoom overlay for Mermaid diagrams ([#107](https://github.com/ff-fab/cosalette/issues/107)) ([398249d](https://github.com/ff-fab/cosalette/commit/398249dffcbff1fd4b808a2631e548ebf0848ef9))
* eagerly instantiate settings in App.__init__ ([#44](https://github.com/ff-fab/cosalette/issues/44)) ([59d1b6f](https://github.com/ff-fab/cosalette/commit/59d1b6fea8a82956b810498db51b706869fcefd0))
* export composite strategy types and add strategies re-export module ([#84](https://github.com/ff-fab/cosalette/issues/84)) ([5ac8946](https://github.com/ff-fab/cosalette/commit/5ac894692e121b90387dd2a1c6cfe073f7ca47a7))
* MedianFilter + OneEuroFilter in Rust with dual-backend tests ([#97](https://github.com/ff-fab/cosalette/issues/97)) ([dd4c42b](https://github.com/ff-fab/cosalette/commit/dd4c42bef745a9eb6c231964e5d0310befdf9297))
* migrate documentation from MkDocs to Zensical ([#21](https://github.com/ff-fab/cosalette/issues/21)) ([674832c](https://github.com/ff-fab/cosalette/commit/674832c1d765a0dffdabf9d740b44e899155e157))
* optional MQTT params in [@app](https://github.com/app).command() + docs update ([#25](https://github.com/ff-fab/cosalette/issues/25)) ([d1a4f25](https://github.com/ff-fab/cosalette/commit/d1a4f253dc2e6b366d99a527467b908accac2370))
* Phase 1 — Foundation modules (Settings, Clock, Logging) ([#2](https://github.com/ff-fab/cosalette/issues/2)) ([3a7d4c0](https://github.com/ff-fab/cosalette/commit/3a7d4c0d8ca1edf8c91789ea4e3ad20efa697e22))
* Phase 4 — App orchestrator, DeviceContext, and TopicRouter ([#8](https://github.com/ff-fab/cosalette/issues/8)) ([2ca3817](https://github.com/ff-fab/cosalette/commit/2ca38178ca4506c0ceff268f1dff9537a7ffcb95))
* pre-release polish — root devices, log rotation, reconnect backoff ([#27](https://github.com/ff-fab/cosalette/issues/27)) ([fb641d6](https://github.com/ff-fab/cosalette/commit/fb641d6bd099c89bdec06a7a59cbe2bea05abb6d))
* Pt1Filter in Rust with dual-backend test parametrization ([#95](https://github.com/ff-fab/cosalette/issues/95)) ([1ceabed](https://github.com/ff-fab/cosalette/commit/1ceabed8db5854a9673e868a2b102e37dec0c14c))
* public API, gate tasks, and integration tests (Phase 7) ([#12](https://github.com/ff-fab/cosalette/issues/12)) ([47e75fb](https://github.com/ff-fab/cosalette/commit/47e75fb3ff1bdf159fd9ff55dc2eb0a6ca38549c))
* recursive leaf-level thresholds and strategy documentation ([#40](https://github.com/ff-fab/cosalette/issues/40)) ([3c7f9cd](https://github.com/ff-fab/cosalette/commit/3c7f9cdcbc2804e2278df1f29f90b4fcaa161fba))
* require Rust filters, drop Python fallback (ADR-022) ([#98](https://github.com/ff-fab/cosalette/issues/98)) ([21bd4b5](https://github.com/ff-fab/cosalette/commit/21bd4b52f120a00cf8ef289d36a845f80d784210))
* Rust project scaffold for cosalette-filters-rs (pyo3/maturin) ([#94](https://github.com/ff-fab/cosalette/issues/94)) ([13f13eb](https://github.com/ff-fab/cosalette/commit/13f13eba95795025edde21fdfc50f663ac31d11b))
* scoped name uniqueness for shared telemetry+command topics ([#66](https://github.com/ff-fab/cosalette/issues/66)) ([a489a3e](https://github.com/ff-fab/cosalette/commit/a489a3e494c560c6b983a513156fb2857ff8392e))
* settings-aware adapter constructors with unified DI pipeline ([#50](https://github.com/ff-fab/cosalette/issues/50)) ([65436ba](https://github.com/ff-fab/cosalette/commit/65436ba7354420c9273a40fd4b095e2f66f7f4d6))
* signal filters utility library (Pt1Filter, MedianFilter, OneEuroFilter) ([#41](https://github.com/ff-fab/cosalette/issues/41)) ([15c3ace](https://github.com/ff-fab/cosalette/commit/15c3acef11bec302b61367bea57bd302bb80b389))
* telemetry error deduplication and health integration ([#29](https://github.com/ff-fab/cosalette/issues/29)) ([80a75bb](https://github.com/ff-fab/cosalette/commit/80a75bb342a46f7bd7d6cd00e592b128c74fd294))


### Bug Fixes

* bypass upstream beads install script WSL URL corruption ([8d2d239](https://github.com/ff-fab/cosalette/commit/8d2d239b08da01a7bb0c7e8d895508949472c528))
* bypass upstream beads install script WSL URL corruption ([d4d832f](https://github.com/ff-fab/cosalette/commit/d4d832f1b45bceabe7e6cd80937e5d857eafb64e))
* cancel adapter entry on shutdown signal ([#56](https://github.com/ff-fab/cosalette/issues/56)) ([4606b21](https://github.com/ff-fab/cosalette/commit/4606b21ed0579a673bfe2c25b104a90db405155a))
* drop file: prefix from syft command to fix glob expansion ([#59](https://github.com/ff-fab/cosalette/issues/59)) ([caa03e6](https://github.com/ff-fab/cosalette/commit/caa03e61e3151820e232aae8bd9d2e54cbde8bcd))
* improve error handling in device proxy and lifespan teardown ([#24](https://github.com/ff-fab/cosalette/issues/24)) ([7c75c27](https://github.com/ff-fab/cosalette/commit/7c75c27c75a5d8488ffe4b2d2d02166176966d95))
* install signal handlers before adapter lifecycle entry ([#55](https://github.com/ff-fab/cosalette/issues/55)) ([29d5351](https://github.com/ff-fab/cosalette/commit/29d535132d2a18dfd291707268d1587631eed131))
* isolate make_settings from ambient environment variables ([#13](https://github.com/ff-fab/cosalette/issues/13)) ([c2d0751](https://github.com/ff-fab/cosalette/commit/c2d0751420b110003d5774eb0466b73528c96c39))
* reject NaN/Inf in filter constructors and update() ([#100](https://github.com/ff-fab/cosalette/issues/100)) ([c3f10ac](https://github.com/ff-fab/cosalette/commit/c3f10ac1e7a96d228b1e62960f5a688da99a6cec))
* replace deprecated macos-13 runner with macos-14 cross-compile ([#108](https://github.com/ff-fab/cosalette/issues/108)) ([239b999](https://github.com/ff-fab/cosalette/commit/239b999a219f850fcca349a5f6485ab0e928610f))
* set #[pyclass(module)] on all pyo3 filter classes ([#99](https://github.com/ff-fab/cosalette/issues/99)) ([becc5dd](https://github.com/ff-fab/cosalette/commit/becc5ddf7bcff325d9f069a475b4fb9fb0d233fd))

## [0.2.0](https://github.com/ff-fab/cosalette/compare/v0.1.8...v0.2.0) (2026-03-13)


### ⚠ BREAKING CHANGES

* require Rust filters, drop Python fallback (ADR-022) ([#98](https://github.com/ff-fab/cosalette/issues/98))

### Features

* abi3 wheels + Node.js 24 actions upgrade ([#103](https://github.com/ff-fab/cosalette/issues/103)) ([fdc9cfe](https://github.com/ff-fab/cosalette/commit/fdc9cfe9880a85dddb6643f334d279169c1625da))
* add --show-devices and --show-devices-json CLI flags ([#92](https://github.com/ff-fab/cosalette/issues/92)) ([1378500](https://github.com/ff-fab/cosalette/commit/1378500cdd9c0f33e7632d21c0b9e28f74037fe6))
* add benchmark suite for hot paths ([#93](https://github.com/ff-fab/cosalette/issues/93)) ([72bdccd](https://github.com/ff-fab/cosalette/commit/72bdccd6d407541716731caa7c06ae17ce60b069))
* add clock-controlled sleep to ClockPort protocol ([#85](https://github.com/ff-fab/cosalette/issues/85)) ([021564b](https://github.com/ff-fab/cosalette/commit/021564bb0363691dae50bd98a4b1c83ebcaaee63))
* add cognitive complexity gate and refactor all violations below threshold ([9c2d88e](https://github.com/ff-fab/cosalette/commit/9c2d88ec67403268db5358882858de9add8ca379)), closes [#71](https://github.com/ff-fab/cosalette/issues/71)
* add MQTT integration tests with testcontainers (COS-0ky) ([#90](https://github.com/ff-fab/cosalette/issues/90)) ([edc45b4](https://github.com/ff-fab/cosalette/commit/edc45b457011c8ee8bdc7df0baa72ee7a67961a6))
* add property-based tests with Hypothesis (COS-rmy) ([#88](https://github.com/ff-fab/cosalette/issues/88)) ([f285c1b](https://github.com/ff-fab/cosalette/commit/f285c1be9714a6e20de9c8ac3f60629b2b0b8605))
* add registry introspection module (_introspect.py) ([#91](https://github.com/ff-fab/cosalette/issues/91)) ([4fa25ef](https://github.com/ff-fab/cosalette/commit/4fa25ef04edf66515d61f059876471090c27d334))
* adopt orjson as hard dependency (COS-gjp) ([#87](https://github.com/ff-fab/cosalette/issues/87)) ([4f123ee](https://github.com/ff-fab/cosalette/commit/4f123ee450b7f5633adb5afc9100edd540f13cdb))
* CI wheel matrix + single maturin package ([#102](https://github.com/ff-fab/cosalette/issues/102)) ([21fa324](https://github.com/ff-fab/cosalette/commit/21fa324d544f35c404fbe9a7f60666ccded7ffec))
* click-to-zoom overlay for Mermaid diagrams ([#107](https://github.com/ff-fab/cosalette/issues/107)) ([398249d](https://github.com/ff-fab/cosalette/commit/398249dffcbff1fd4b808a2631e548ebf0848ef9))
* export composite strategy types and add strategies re-export module ([#84](https://github.com/ff-fab/cosalette/issues/84)) ([5ac8946](https://github.com/ff-fab/cosalette/commit/5ac894692e121b90387dd2a1c6cfe073f7ca47a7))
* MedianFilter + OneEuroFilter in Rust with dual-backend tests ([#97](https://github.com/ff-fab/cosalette/issues/97)) ([dd4c42b](https://github.com/ff-fab/cosalette/commit/dd4c42bef745a9eb6c231964e5d0310befdf9297))
* Pt1Filter in Rust with dual-backend test parametrization ([#95](https://github.com/ff-fab/cosalette/issues/95)) ([1ceabed](https://github.com/ff-fab/cosalette/commit/1ceabed8db5854a9673e868a2b102e37dec0c14c))
* require Rust filters, drop Python fallback (ADR-022) ([#98](https://github.com/ff-fab/cosalette/issues/98)) ([21bd4b5](https://github.com/ff-fab/cosalette/commit/21bd4b52f120a00cf8ef289d36a845f80d784210))
* Rust project scaffold for cosalette-filters-rs (pyo3/maturin) ([#94](https://github.com/ff-fab/cosalette/issues/94)) ([13f13eb](https://github.com/ff-fab/cosalette/commit/13f13eba95795025edde21fdfc50f663ac31d11b))


### Bug Fixes

* bypass upstream beads install script WSL URL corruption ([8d2d239](https://github.com/ff-fab/cosalette/commit/8d2d239b08da01a7bb0c7e8d895508949472c528))
* bypass upstream beads install script WSL URL corruption ([d4d832f](https://github.com/ff-fab/cosalette/commit/d4d832f1b45bceabe7e6cd80937e5d857eafb64e))
* reject NaN/Inf in filter constructors and update() ([#100](https://github.com/ff-fab/cosalette/issues/100)) ([c3f10ac](https://github.com/ff-fab/cosalette/commit/c3f10ac1e7a96d228b1e62960f5a688da99a6cec))
* set #[pyclass(module)] on all pyo3 filter classes ([#99](https://github.com/ff-fab/cosalette/issues/99)) ([becc5dd](https://github.com/ff-fab/cosalette/commit/becc5ddf7bcff325d9f069a475b4fb9fb0d233fd))

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
