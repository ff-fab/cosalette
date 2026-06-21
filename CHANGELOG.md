# Changelog

## [0.5.0](https://github.com/ff-fab/cosalette/compare/v0.4.1...v0.5.0) (2026-06-21)


### ⚠ BREAKING CHANGES

* **stream:** AsyncStreamablePort[T] is removed. All stream adapters must now implement the single StreamablePort[T] protocol with async lifecycle methods (async def open/close/start_scan/stop_scan). The registration key is StreamablePort[T] in all cases.
* add sub-topic routing for command handlers ([#131](https://github.com/ff-fab/cosalette/issues/131))
* require Rust filters, drop Python fallback (ADR-022) ([#98](https://github.com/ff-fab/cosalette/issues/98))
* API ergonomics - app.run(), lifespan, injection ([#22](https://github.com/ff-fab/cosalette/issues/22))

### Features

* :sparkles: initial commit ([bf3238e](https://github.com/ff-fab/cosalette/commit/bf3238ed449ac1cf074f365032751bbf00f3da15))
* abi3 wheels + Node.js 24 actions upgrade ([#103](https://github.com/ff-fab/cosalette/issues/103)) ([8f1b2ac](https://github.com/ff-fab/cosalette/commit/8f1b2ac08da30aad41ec5942f7a58c08ed88b451))
* adapter factory settings injection ([#36](https://github.com/ff-fab/cosalette/issues/36)) ([7df4c92](https://github.com/ff-fab/cosalette/commit/7df4c92c1740e759db18504fb4ec89587e78d151))
* add --show-devices and --show-devices-json CLI flags ([#92](https://github.com/ff-fab/cosalette/issues/92)) ([f0844a5](https://github.com/ff-fab/cosalette/commit/f0844a5d4ae1fedb360f29bec5a1ac4df01732c0))
* add [@app](https://github.com/app).command() decorator for command handler registration ([#23](https://github.com/ff-fab/cosalette/issues/23)) ([b98e336](https://github.com/ff-fab/cosalette/commit/b98e3364df0165de6ec246cf1b3260e18e2f3b7f))
* add [@app](https://github.com/app).on_configure lifecycle hook and dict-name multi-device registration ([#124](https://github.com/ff-fab/cosalette/issues/124)) ([b4a95bf](https://github.com/ff-fab/cosalette/commit/b4a95bfb4416ba8babb712371afd333bd74bdc94))
* add [@app](https://github.com/app).state lifespan-scoped shared-state factory ([#247](https://github.com/ff-fab/cosalette/issues/247)) ([bf15d61](https://github.com/ff-fab/cosalette/commit/bf15d61c8d5eae2e4f8abf58ad67c3048d5bff9a))
* add adapter auto-restart on health check failure ([#143](https://github.com/ff-fab/cosalette/issues/143)) ([0a2093e](https://github.com/ff-fab/cosalette/commit/0a2093e9cc16bc98fac2cd0470f7400c77704fa9))
* add adapters= dict parameter to App constructor ([#53](https://github.com/ff-fab/cosalette/issues/53)) ([30f3c8b](https://github.com/ff-fab/cosalette/commit/30f3c8be2354853dd7355d2a78eb0dfcd82d5eff))
* add benchmark suite for hot paths ([#93](https://github.com/ff-fab/cosalette/issues/93)) ([85f7f47](https://github.com/ff-fab/cosalette/commit/85f7f479971525ee4b2c713fc79bb8d5a089168e))
* add brand identity assets and wire into docs site ([#148](https://github.com/ff-fab/cosalette/issues/148)) ([a4ed50e](https://github.com/ff-fab/cosalette/commit/a4ed50e5568791d769ef97992b2a75854c89a6a4))
* add canonical AsyncAPI introspection ([#286](https://github.com/ff-fab/cosalette/issues/286)) ([38c12b2](https://github.com/ff-fab/cosalette/commit/38c12b2f843cd38bf68e01f0edf6b99991a57f99))
* add CLI scaffolding with Typer ([#9](https://github.com/ff-fab/cosalette/issues/9)) ([8b36d5f](https://github.com/ff-fab/cosalette/commit/8b36d5f9f0e6f96c470d37b096df8963855dd596))
* add clock-controlled sleep to ClockPort protocol ([#85](https://github.com/ff-fab/cosalette/issues/85)) ([313659d](https://github.com/ff-fab/cosalette/commit/313659dca4b286ea0a3ef4f53546a9b9f8feb9c2))
* add cognitive complexity gate and refactor all violations below threshold ([9301f4c](https://github.com/ff-fab/cosalette/commit/9301f4c99e8966aeef80d08e4a0dcff1ce36ebca)), closes [#71](https://github.com/ff-fab/cosalette/issues/71)
* add Command dataclass and per-device command queue ([#128](https://github.com/ff-fab/cosalette/issues/128)) ([fc0a4b4](https://github.com/ff-fab/cosalette/commit/fc0a4b4991802fdf1257471ee18c72ad069cacd1))
* add cosalette.testing module (Phase 6) ([#11](https://github.com/ff-fab/cosalette/issues/11)) ([6adfd28](https://github.com/ff-fab/cosalette/commit/6adfd28c074724381ae4c866bf0469dd1227a186))
* add coverage and test count badges to README ([#115](https://github.com/ff-fab/cosalette/issues/115)) ([40e2c69](https://github.com/ff-fab/cosalette/commit/40e2c692f040ed3412861e559c350bc206d60273))
* add ctx.commands() async iterator for device loops ([#129](https://github.com/ff-fab/cosalette/issues/129)) ([37aef84](https://github.com/ff-fab/cosalette/commit/37aef8486c9892100cb0db590c74feb648617a45))
* add enabled= parameter for conditional device registration ([#52](https://github.com/ff-fab/cosalette/issues/52)) ([9bb2958](https://github.com/ff-fab/cosalette/commit/9bb295817a90aebcb35d288eea2738d300985510))
* add error publisher and health reporter (_errors.py, _health.py) ([#5](https://github.com/ff-fab/cosalette/issues/5)) ([a4b53ae](https://github.com/ff-fab/cosalette/commit/a4b53ae6f02be00951209a3149c85fd61d82172a))
* add HealthCheckable protocol and adapter detection ([#136](https://github.com/ff-fab/cosalette/issues/136)) ([22e9a4a](https://github.com/ff-fab/cosalette/commit/22e9a4a41ea165b1b826a632e9c7306360b8d707))
* add hero banners, social preview, and brand asset scripts ([#150](https://github.com/ff-fab/cosalette/issues/150)) ([ab75abe](https://github.com/ff-fab/cosalette/commit/ab75abe63d75c97abe47cbe9ed43d58d02ec7352))
* add imperative add_device/add_telemetry/add_command methods ([#51](https://github.com/ff-fab/cosalette/issues/51)) ([2ba8af0](https://github.com/ff-fab/cosalette/commit/2ba8af011e2f937b709ea2f4c6e97ce044f66c3a))
* add init= callback for per-device state injection ([#46](https://github.com/ff-fab/cosalette/issues/46)) ([69ff842](https://github.com/ff-fab/cosalette/commit/69ff842a4cf9cce8a7b227eab2d709a136456bd8))
* add IntervalSpec type for deferred telemetry interval resolution ([#68](https://github.com/ff-fab/cosalette/issues/68)) ([2b3d39f](https://github.com/ff-fab/cosalette/commit/2b3d39f723376bbdc77690bc431b1db044163e80))
* add lifespan-yielded injectable state (ADR-027) ([#134](https://github.com/ff-fab/cosalette/issues/134)) ([877b1d6](https://github.com/ff-fab/cosalette/commit/877b1d6837ad1ec2170841a820a12719895876ed))
* add MQTT client port and adapters (_mqtt.py) ([#3](https://github.com/ff-fab/cosalette/issues/3)) ([fddb29c](https://github.com/ff-fab/cosalette/commit/fddb29cb7684620a0a34fb48b8b30f4a8bd706c9))
* add MQTT integration tests with testcontainers (COS-0ky) ([#90](https://github.com/ff-fab/cosalette/issues/90)) ([dcb87a0](https://github.com/ff-fab/cosalette/commit/dcb87a078009a5ea0a01983813c5a304f8634cac))
* add Open Graph meta tags, custom 404 page, and PyPI badge icon ([#152](https://github.com/ff-fab/cosalette/issues/152)) ([ea854bd](https://github.com/ff-fab/cosalette/commit/ea854bdf606ffc835a030f263a19acf3ebdb50c3))
* add opencode.ai and kilo.ai support to cosalette ai init ([#258](https://github.com/ff-fab/cosalette/issues/258)) ([d5e9398](https://github.com/ff-fab/cosalette/commit/d5e9398069f03cade31fbfe46d405d01993f1951))
* add periodic health check runner with availability toggling ([#137](https://github.com/ff-fab/cosalette/issues/137)) ([f175483](https://github.com/ff-fab/cosalette/commit/f175483c8558cdfbdcb2144b53c7337eb6b9cec1))
* add periodic heartbeat scheduling (opt-in, default 60s) ([#19](https://github.com/ff-fab/cosalette/issues/19)) ([9996663](https://github.com/ff-fab/cosalette/commit/999666335365e108b368da7d721dbf26585c1e18))
* add persistence system with Store protocol, DeviceStore, and save policies ([#47](https://github.com/ff-fab/cosalette/issues/47)) ([b317319](https://github.com/ff-fab/cosalette/commit/b3173191f41113ef4c94c3e3a418249867c9c345))
* add PR-specific banner to docs preview builds ([132eabd](https://github.com/ff-fab/cosalette/commit/132eabd016f6aba44b8bd79d6b571464384d6457))
* add property-based tests with Hypothesis (COS-rmy) ([#88](https://github.com/ff-fab/cosalette/issues/88)) ([0479276](https://github.com/ff-fab/cosalette/commit/047927643d087db786ab6fbfca2fa5cb2febf4f8))
* add publish strategies for telemetry devices ([#38](https://github.com/ff-fab/cosalette/issues/38)) ([01a7b1d](https://github.com/ff-fab/cosalette/commit/01a7b1d69eb60ffa31ea2ef5d54205cc6f3ce441))
* add registry introspection module (_introspect.py) ([#91](https://github.com/ff-fab/cosalette/issues/91)) ([2342d01](https://github.com/ff-fab/cosalette/commit/2342d01424171600ebcfafee8e6c5d2a761bdcea))
* add schedule= parameter and ctx.sleep_until() ([94c7959](https://github.com/ff-fab/cosalette/commit/94c7959c761d71c9721007356ba3cc0e5cf30e02))
* add schedule= parameter and ctx.sleep_until() ([84a2169](https://github.com/ff-fab/cosalette/commit/84a21694c158495e5e82c1b74c99ee87f7f055c6))
* add schema data model and AsyncAPI 3.0.0 loader ([#174](https://github.com/ff-fab/cosalette/issues/174)) ([c5e9b20](https://github.com/ff-fab/cosalette/commit/c5e9b20d183e693480b93d1492a4ba6e8712123d))
* add state reactors ([#279](https://github.com/ff-fab/cosalette/issues/279)) ([cce9072](https://github.com/ff-fab/cosalette/commit/cce9072e56f24b18a58f3f9491c098d1ec7472e2))
* add sub-topic routing for command handlers ([#131](https://github.com/ff-fab/cosalette/issues/131)) ([06d320e](https://github.com/ff-fab/cosalette/commit/06d320ebf1396ae627ae21899b8348c76c10f75d))
* add telemetry coalescing groups ([#62](https://github.com/ff-fab/cosalette/issues/62)) ([50e9972](https://github.com/ff-fab/cosalette/commit/50e99727775ffe7791d644c718962f99e5d06c65))
* add triggerable telemetry with TriggerPayload injectable ([#214](https://github.com/ff-fab/cosalette/issues/214)) ([c51efe2](https://github.com/ff-fab/cosalette/commit/c51efe25d5cbab4728b4cbe7270f627ed8d2a498))
* add typed handler contracts ([#285](https://github.com/ff-fab/cosalette/issues/285)) ([1e6e0be](https://github.com/ff-fab/cosalette/commit/1e6e0bee5fb4e0f402447ec8203ebc40ba6858e8))
* adopt orjson as hard dependency (COS-gjp) ([#87](https://github.com/ff-fab/cosalette/issues/87)) ([6754f52](https://github.com/ff-fab/cosalette/commit/6754f52975f017ed25fc4fda0fe7c5a078d2e208))
* **ai:** day-1 DX improvements from v0.3.1 adoption ([#211](https://github.com/ff-fab/cosalette/issues/211)) ([9271b5b](https://github.com/ff-fab/cosalette/commit/9271b5bf837fc6aabde583351d9d2bce59c5f291))
* **ai:** promote multi-device name=callable to all AI surfaces ([#219](https://github.com/ff-fab/cosalette/issues/219)) ([8d1c2e0](https://github.com/ff-fab/cosalette/commit/8d1c2e0a866dec41f4ab658fd3766badcc1ee6aa))
* API ergonomics - app.run(), lifespan, injection ([#22](https://github.com/ff-fab/cosalette/issues/22)) ([f91668f](https://github.com/ff-fab/cosalette/commit/f91668fc5d9cfa0391dea5fed24743702a24c3f6))
* **app:** add [@app](https://github.com/app).periodic background interval tasks ([#250](https://github.com/ff-fab/cosalette/issues/250)) ([0f591a0](https://github.com/ff-fab/cosalette/commit/0f591a086b6b7d479a5b7325971231cd542067aa))
* auto-manage adapter lifecycle via async context manager protocol ([#49](https://github.com/ff-fab/cosalette/issues/49)) ([ceb07c3](https://github.com/ff-fab/cosalette/commit/ceb07c352d19bb9500a082ccdddf3af99ac2edc1))
* auto-publish registry snapshot and prune cancelled tasks ([#166](https://github.com/ff-fab/cosalette/issues/166)) ([58e56a8](https://github.com/ff-fab/cosalette/commit/58e56a88e9a476a94e6827bc32cadea47e369827))
* bind agent models and add docs-subagent ([#163](https://github.com/ff-fab/cosalette/issues/163)) ([c495e2e](https://github.com/ff-fab/cosalette/commit/c495e2e580eb3c570713c5c7e69a8820eb463b4d))
* brand assets round 2 — logotype, favicon bg, theme logo ([#149](https://github.com/ff-fab/cosalette/issues/149)) ([369108b](https://github.com/ff-fab/cosalette/commit/369108bbdafde728f9597e2087755e5ed58642f2))
* CI wheel matrix + single maturin package ([#102](https://github.com/ff-fab/cosalette/issues/102)) ([e52d07f](https://github.com/ff-fab/cosalette/commit/e52d07f5c56ccf73fe377df3a91a12c9627aa86c))
* click-to-zoom overlay for Mermaid diagrams ([#107](https://github.com/ff-fab/cosalette/issues/107)) ([ee1f1ad](https://github.com/ff-fab/cosalette/commit/ee1f1adbf51f425e683ee77a83ea38680d3f8df3))
* **commands:** add sub-dispatch routing (sub=, sub_key=) ([#249](https://github.com/ff-fab/cosalette/issues/249)) ([5021ce7](https://github.com/ff-fab/cosalette/commit/5021ce728d73125d3a5d14ef004f29485a59615d))
* configurable retry/backoff on [@app](https://github.com/app).telemetry (ADR-024) ([#126](https://github.com/ff-fab/cosalette/issues/126)) ([3733577](https://github.com/ff-fab/cosalette/commit/37335770d8c8aa4b842166d34544c43aaf733876))
* contract metadata on [@app](https://github.com/app).device() and add_device() ([#227](https://github.com/ff-fab/cosalette/issues/227)) ([d37c222](https://github.com/ff-fab/cosalette/commit/d37c222031d7a7d814f82abfb41becaab811fc74))
* contract-first MQTT framework enhancement (E1-E5) ([#225](https://github.com/ff-fab/cosalette/issues/225)) ([027c1ca](https://github.com/ff-fab/cosalette/commit/027c1ca52094bb75a70be0ef24c703880bc89af9))
* **cos-1kl:** enable mode=max BuildKit registry cache ([#261](https://github.com/ff-fab/cosalette/issues/261)) ([d48b980](https://github.com/ff-fab/cosalette/commit/d48b980171747da43da8a18df382e71cd9209acd))
* **cos-6c3:** move Rust to Dockerfile for BuildKit layer caching ([#260](https://github.com/ff-fab/cosalette/issues/260)) ([598154c](https://github.com/ff-fab/cosalette/commit/598154c4ee9d3e484cfd18bab74aae4be9a4407b))
* **cos-762:** move docker-in-docker from feature to Dockerfile ([#262](https://github.com/ff-fab/cosalette/issues/262)) ([f5ed9c2](https://github.com/ff-fab/cosalette/commit/f5ed9c218f2c3469c887346e977929eecc997129))
* cos-qn1 · cos-089 · cos-9et · cos-hld ([#288](https://github.com/ff-fab/cosalette/issues/288)) ([9eba0de](https://github.com/ff-fab/cosalette/commit/9eba0de998372ec8d2d7a367e02aeba583aa0d5a))
* **devcontainer:** add OCI image description labels ([#267](https://github.com/ff-fab/cosalette/issues/267)) ([79c1467](https://github.com/ff-fab/cosalette/commit/79c1467abdb080d1d5c67cd7620c7a4316640c4d))
* docs feedback widget, centered badges, PR template & subagent ([#200](https://github.com/ff-fab/cosalette/issues/200)) ([1825341](https://github.com/ff-fab/cosalette/commit/182534128c86f2304b2b2e55931c0804f14d13e8))
* eagerly instantiate settings in App.__init__ ([#44](https://github.com/ff-fab/cosalette/issues/44)) ([3979cd2](https://github.com/ff-fab/cosalette/commit/3979cd266691864a74bd714f69be0690f333a250))
* Epic 8 — dynamic sub-entity availability (ADR-031) ([#158](https://github.com/ff-fab/cosalette/issues/158)) ([6b65b54](https://github.com/ff-fab/cosalette/commit/6b65b54c0a29753bc75a96d7ab93f1946f675e32))
* export composite strategy types and add strategies re-export module ([#84](https://github.com/ff-fab/cosalette/issues/84)) ([dc25fdf](https://github.com/ff-fab/cosalette/commit/dc25fdfbacdd7c858425bdedb88c1ddd252a3db6))
* Group C follow-ups — type widening, AsyncExitStack, bounded queue ([#253](https://github.com/ff-fab/cosalette/issues/253)) ([b675739](https://github.com/ff-fab/cosalette/commit/b675739857254068e8336d8a0efb72eedddc8fc6))
* harden security posture ([#275](https://github.com/ff-fab/cosalette/issues/275)) ([856ecde](https://github.com/ff-fab/cosalette/commit/856ecdeb8040db110568eb51d14e5befd98a33be))
* hero responsive CSS and Mermaid brand color alignment ([#153](https://github.com/ff-fab/cosalette/issues/153)) ([5030e2c](https://github.com/ff-fab/cosalette/commit/5030e2c48fc3a0ab5b2ef559f546381dbc4e33a2))
* implement [@app](https://github.com/app).stream decorator with full lifecycle integration ([#252](https://github.com/ff-fab/cosalette/issues/252)) ([0eaa52e](https://github.com/ff-fab/cosalette/commit/0eaa52e236bb6dc31b9ce7ff1e7dcd9c21b626b8))
* lazy store resolution (FEP-001) ([#222](https://github.com/ff-fab/cosalette/issues/222)) ([842738e](https://github.com/ff-fab/cosalette/commit/842738ec5c215bc090c0551c34876b81c945d453))
* MCP scaffolding tools (F4) — completes cos-0e5 epic ([#190](https://github.com/ff-fab/cosalette/issues/190)) ([e384a83](https://github.com/ff-fab/cosalette/commit/e384a832a7a21b2f33848a9369e000e5dfa7a32f))
* MedianFilter + OneEuroFilter in Rust with dual-backend tests ([#97](https://github.com/ff-fab/cosalette/issues/97)) ([dd8c2d4](https://github.com/ff-fab/cosalette/commit/dd8c2d40f77b63d6a98ba25987512cf8d00b32f0))
* migrate documentation from MkDocs to Zensical ([#21](https://github.com/ff-fab/cosalette/issues/21)) ([31766af](https://github.com/ff-fab/cosalette/commit/31766afc68321381f2ccbf590258768ed49a919a))
* optimize agent orchestration system ([#160](https://github.com/ff-fab/cosalette/issues/160)) ([03da8be](https://github.com/ff-fab/cosalette/commit/03da8be77b177dcc587324fc6da603612bb023fe))
* optional MCP server for downstream AI support (F1-F3, F5) ([#189](https://github.com/ff-fab/cosalette/issues/189)) ([a68b0a5](https://github.com/ff-fab/cosalette/commit/a68b0a57114d9b7c73d2023a732347e0cf38ebeb))
* optional MQTT params in [@app](https://github.com/app).command() + docs update ([#25](https://github.com/ff-fab/cosalette/issues/25)) ([1fece92](https://github.com/ff-fab/cosalette/commit/1fece927bdf20f9ad2c10f165e35d5a5b4410ba3))
* per-device callable schedule= for name=callable telemetry ([#237](https://github.com/ff-fab/cosalette/issues/237)) ([a29d303](https://github.com/ff-fab/cosalette/commit/a29d303f4018ba242ebaaf96030f4b3ae7a175cd))
* Phase 1 — Foundation modules (Settings, Clock, Logging) ([#2](https://github.com/ff-fab/cosalette/issues/2)) ([bf8932d](https://github.com/ff-fab/cosalette/commit/bf8932d68607059d79c3894067bf9942cdd73b65))
* Phase 4 — App orchestrator, DeviceContext, and TopicRouter ([#8](https://github.com/ff-fab/cosalette/issues/8)) ([dcaa278](https://github.com/ff-fab/cosalette/commit/dcaa2789cf756f18673e83584fa7996b4cad1f6c))
* pre-release polish — root devices, log rotation, reconnect backoff ([#27](https://github.com/ff-fab/cosalette/issues/27)) ([3391040](https://github.com/ff-fab/cosalette/commit/33910401e79b95edaf8b61ee9dc4cb454b232018))
* Pt1Filter in Rust with dual-backend test parametrization ([#95](https://github.com/ff-fab/cosalette/issues/95)) ([a1e272c](https://github.com/ff-fab/cosalette/commit/a1e272c9237f342446769e58a95d51f47d74c136))
* public API, gate tasks, and integration tests (Phase 7) ([#12](https://github.com/ff-fab/cosalette/issues/12)) ([a126b3e](https://github.com/ff-fab/cosalette/commit/a126b3e6bc160d6bda027f32f2c2508f894dcea6))
* recursive leaf-level thresholds and strategy documentation ([#40](https://github.com/ff-fab/cosalette/issues/40)) ([16d92b1](https://github.com/ff-fab/cosalette/commit/16d92b17483368e3b864cbc5ecc1b656383d1541))
* require Rust filters, drop Python fallback (ADR-022) ([#98](https://github.com/ff-fab/cosalette/issues/98)) ([1bb4f6e](https://github.com/ff-fab/cosalette/commit/1bb4f6ed240cbe677aeb7b25cd110797a29e61bf))
* **router:** add public router composition ([e519398](https://github.com/ff-fab/cosalette/commit/e5193988d5ca568ccbd349d30beaf4d4d2db2398))
* Rust project scaffold for cosalette-filters-rs (pyo3/maturin) ([#94](https://github.com/ff-fab/cosalette/issues/94)) ([59a3ab3](https://github.com/ff-fab/cosalette/commit/59a3ab31e0e53d1a53450f55c9aaaae1e7228624))
* schema CLI tooling (validate, check, dump, init, slice) ([#176](https://github.com/ff-fab/cosalette/issues/176)) ([4279655](https://github.com/ff-fab/cosalette/commit/42796555de9fbf0982cbfdc89a3e56fc3650f2b0))
* schema enforcement lifecycle integration + beads sync fix ([#175](https://github.com/ff-fab/cosalette/issues/175)) ([ae7cbe0](https://github.com/ff-fab/cosalette/commit/ae7cbe08a2511c387fe9d7f74c87545a17357488))
* schema enforcement Phase V — runtime validation and monitoring ([#182](https://github.com/ff-fab/cosalette/issues/182)) ([01740b9](https://github.com/ff-fab/cosalette/commit/01740b93d14351c0326a685106ef48e4da8de6ad))
* schema-driven ADR creation with JSON validation and rendering ([#168](https://github.com/ff-fab/cosalette/issues/168)) ([941e93f](https://github.com/ff-fab/cosalette/commit/941e93f965043793fbbd25e00eff6f27a4225b3f))
* scoped name uniqueness for shared telemetry+command topics ([#66](https://github.com/ff-fab/cosalette/issues/66)) ([c1fefdc](https://github.com/ff-fab/cosalette/commit/c1fefdc284078179e32b18c586c717b7def4687d))
* settings-aware adapter constructors with unified DI pipeline ([#50](https://github.com/ff-fab/cosalette/issues/50)) ([23d169e](https://github.com/ff-fab/cosalette/commit/23d169e95a3718c1cadcc9b8b878d487da890031))
* signal filters utility library (Pt1Filter, MedianFilter, OneEuroFilter) ([#41](https://github.com/ff-fab/cosalette/issues/41)) ([c5a69e5](https://github.com/ff-fab/cosalette/commit/c5a69e5ab6870c27c55d307d58f85c6fec5374d3))
* **stream:** add StreamablePort and Stream async bridge ([#251](https://github.com/ff-fab/cosalette/issues/251)) ([4aca615](https://github.com/ff-fab/cosalette/commit/4aca615e8b7225b24b27c1808dd7364fecd3c6bf))
* **stream:** stateful stream receiver runtime parity ([#283](https://github.com/ff-fab/cosalette/issues/283)) ([380a59e](https://github.com/ff-fab/cosalette/commit/380a59ea5c80dbdaa85849b1bdc2b8f2383ab46c))
* telemetry error deduplication and health integration ([#29](https://github.com/ff-fab/cosalette/issues/29)) ([de27533](https://github.com/ff-fab/cosalette/commit/de2753380a702d27ec452564a294fcbed757d425))
* **telemetry:** allow triggerable=True with callable name= ([#236](https://github.com/ff-fab/cosalette/issues/236)) ([686131e](https://github.com/ff-fab/cosalette/commit/686131e7f16c05eccdc23164fe3b0b8e0936c26e))
* **testing:** add harness helpers and docs ([#287](https://github.com/ff-fab/cosalette/issues/287)) ([4e62420](https://github.com/ff-fab/cosalette/commit/4e624200c4d9fc4ab815a092192c18c17e414948))
* update brand identity brief and add docs hero illustration ([#151](https://github.com/ff-fab/cosalette/issues/151)) ([f68e925](https://github.com/ff-fab/cosalette/commit/f68e9250eeba8c164fa499bca0ad8369471a6e99))
* wire commands() queue dispatch and add integration tests ([#130](https://github.com/ff-fab/cosalette/issues/130)) ([e543087](https://github.com/ff-fab/cosalette/commit/e5430874eefc68989ee3cfbcb1f212a08b242a74))


### Bug Fixes

* add continue-on-error to TestPyPI publish step ([78e81ef](https://github.com/ff-fab/cosalette/commit/78e81effc8e6a373c6374738faf7b488507a8587))
* add docker-container buildx driver to devcontainer CI jobs ([#263](https://github.com/ff-fab/cosalette/issues/263)) ([96a6db9](https://github.com/ff-fab/cosalette/commit/96a6db9c8491432d423d11f14abfb15aaef437e5))
* add shutdown-event race guard to enter_restartable_adapters ([#144](https://github.com/ff-fab/cosalette/issues/144)) ([bbb4bcd](https://github.com/ff-fab/cosalette/commit/bbb4bcd4550e6e2c525b599768a69ab1a6cb890c))
* add skip-existing for TestPyPI publish ([a2ef466](https://github.com/ff-fab/cosalette/commit/a2ef4664a5d64ca5c999951f4a7e098dc2faf79d))
* add workflow_dispatch trigger and release-as 0.2.0 override ([566ab29](https://github.com/ff-fab/cosalette/commit/566ab2956dc79425e7b56b11377f28a7b9d66682))
* address review findings for schedule/cron feature ([f47c053](https://github.com/ff-fab/cosalette/commit/f47c053dd0f2f125dca227f3e16f9cc423cfc20d))
* allow docs-only PRs to pass required CI checks ([#122](https://github.com/ff-fab/cosalette/issues/122)) ([67e51c8](https://github.com/ff-fab/cosalette/commit/67e51c84066909326cf78843f185b4ec73e7cedb))
* avoid cancelling shared coalescing group task on single-adapter restart ([#146](https://github.com/ff-fab/cosalette/issues/146)) ([cdf9d2e](https://github.com/ff-fab/cosalette/commit/cdf9d2e66db977855977df434e2c1317bc762c22))
* bypass upstream beads install script WSL URL corruption ([fbc46b0](https://github.com/ff-fab/cosalette/commit/fbc46b062ac0e09d1e6dd1e0a634b6536cc40323))
* bypass upstream beads install script WSL URL corruption ([68e777e](https://github.com/ff-fab/cosalette/commit/68e777eb97590718cbabf50ebba58297b97c7b23))
* cancel adapter entry on shutdown signal ([#56](https://github.com/ff-fab/cosalette/issues/56)) ([a0062c9](https://github.com/ff-fab/cosalette/commit/a0062c907d9f34c03641c859e2dcdd8aa4284420))
* checkout release commit by SHA instead of tag name ([#208](https://github.com/ff-fab/cosalette/issues/208)) ([b1b21cc](https://github.com/ff-fab/cosalette/commit/b1b21cc377a49aeeb33ea1d26fc674f00113615b))
* **ci:** address docs workflow review findings ([#278](https://github.com/ff-fab/cosalette/issues/278)) ([63151a3](https://github.com/ff-fab/cosalette/commit/63151a3bcb783f457acf6d37528b41821d578166))
* **ci:** replace broken replace() in workflow URLs ([#220](https://github.com/ff-fab/cosalette/issues/220)) ([680d00a](https://github.com/ff-fab/cosalette/commit/680d00ae2e820b15e59c649b51b6183e7ba4f126))
* **ci:** skip docs build for non-docs PRs and release-please ([#277](https://github.com/ff-fab/cosalette/issues/277)) ([6b33cc0](https://github.com/ff-fab/cosalette/commit/6b33cc08555f559ab815058c69d04c60016f3565))
* **ci:** skip heavy jobs for release-please PRs ([#276](https://github.com/ff-fab/cosalette/issues/276)) ([6cb2e3d](https://github.com/ff-fab/cosalette/commit/6cb2e3d5cd64350bba3373e61aa525542389308e))
* collapse multiline SKILL.md descriptions and fix frontmatter typos ([6a06b63](https://github.com/ff-fab/cosalette/commit/6a06b635258de1645cc3a31fb90aff7b06cd7b57))
* collapse multiline SKILL.md descriptions and fix frontmatter typos ([32cc7d8](https://github.com/ff-fab/cosalette/commit/32cc7d8006268bedefa33a50d501c7dbb27f6ac7))
* complete partial-handler coverage across introspect, injection, utils ([#243](https://github.com/ff-fab/cosalette/issues/243)) ([96521aa](https://github.com/ff-fab/cosalette/commit/96521aa13094fcb1d872db46dc940365361b7b31))
* **deps:** bump idna 3.17 and pymdown-extensions 10.21.3 (security) ([#297](https://github.com/ff-fab/cosalette/issues/297)) ([bc32704](https://github.com/ff-fab/cosalette/commit/bc327046257506d8357d80564eb96c23b953c6b1))
* **deps:** fix all security vulnerabilities to unblock dependabot PRs ([#306](https://github.com/ff-fab/cosalette/issues/306)) ([138bb92](https://github.com/ff-fab/cosalette/commit/138bb929f13b7fcaf0ff9aa9ddebc1186293fc72))
* **docs:** extend contract-first guide to cover [@app](https://github.com/app).device metadata ([#233](https://github.com/ff-fab/cosalette/issues/233)) ([cf0c186](https://github.com/ff-fab/cosalette/commit/cf0c1861b7031e95e9a81f7d4419bbf65ba1b98d))
* **docs:** use absolute URLs for hero image in README ([#231](https://github.com/ff-fab/cosalette/issues/231)) ([bcebf89](https://github.com/ff-fab/cosalette/commit/bcebf896313fcd911335fbf6fdcab297c049a807))
* **docs:** use light banner as PyPI fallback hero image ([#234](https://github.com/ff-fab/cosalette/issues/234)) ([a6afe02](https://github.com/ff-fab/cosalette/commit/a6afe0250ce10f20e9e05613ca72ddaca72b2b56))
* drop file: prefix from syft command to fix glob expansion ([#59](https://github.com/ff-fab/cosalette/issues/59)) ([bfa395d](https://github.com/ff-fab/cosalette/commit/bfa395d7f41ef485c9dbb924635aad2dd19919b6))
* hide excluded directories from docs navigation ([#206](https://github.com/ff-fab/cosalette/issues/206)) ([fbba040](https://github.com/ff-fab/cosalette/commit/fbba040a23ff92c102e2b73deef49d6f8dfefa55))
* improve commands() shutdown responsiveness for long timeouts ([#140](https://github.com/ff-fab/cosalette/issues/140)) ([1cbe466](https://github.com/ff-fab/cosalette/commit/1cbe4663777932dd93cfba4a8827b007d79b226e))
* improve error handling in device proxy and lifespan teardown ([#24](https://github.com/ff-fab/cosalette/issues/24)) ([da48b3b](https://github.com/ff-fab/cosalette/commit/da48b3ba816d598e57eb5c14626742b090dde3a1))
* include LICENSE in sdist and use non-interactive gist update ([#116](https://github.com/ff-fab/cosalette/issues/116)) ([08f09d8](https://github.com/ff-fab/cosalette/commit/08f09d81e259941d4f78c09f1f8ed31358d8d73b))
* install signal handlers before adapter lifecycle entry ([#55](https://github.com/ff-fab/cosalette/issues/55)) ([bc65a1d](https://github.com/ff-fab/cosalette/commit/bc65a1dc3acb22e29b74cd68caad706f760d1424))
* isolate make_settings from ambient environment variables ([#13](https://github.com/ff-fab/cosalette/issues/13)) ([a21db88](https://github.com/ff-fab/cosalette/commit/a21db8858cf1f3b9dad08d989170eec9ce8cac8b))
* make TestPyPI non-blocking for PyPI publish ([20c8e9f](https://github.com/ff-fab/cosalette/commit/20c8e9fb7e841b233fb89129a8db3a254b16dd2e))
* pre-0.3.0 framework review — strengthen settings validation and close test gaps ([#169](https://github.com/ff-fab/cosalette/issues/169)) ([bc41886](https://github.com/ff-fab/cosalette/commit/bc41886af1601af7d158b15507affbbb17d95c97))
* prevent silent docs build failures ([#141](https://github.com/ff-fab/cosalette/issues/141)) ([f270896](https://github.com/ff-fab/cosalette/commit/f270896b695f580467f0b5a4f825069324fa9404))
* push devcontainer image explicitly, not via devcontainers/ci ([#265](https://github.com/ff-fab/cosalette/issues/265)) ([8a8900d](https://github.com/ff-fab/cosalette/commit/8a8900daaf053fd731405aa7b61f85f0c989e43c))
* reduce _scaffold_device_impl cyclomatic complexity to B ([1f92071](https://github.com/ff-fab/cosalette/commit/1f92071249d16352ea8d8f87b524dd8c668f4d1c))
* reject NaN/Inf in filter constructors and update() ([#100](https://github.com/ff-fab/cosalette/issues/100)) ([db5676d](https://github.com/ff-fab/cosalette/commit/db5676dc3e49a7f00ae9a20966ae4588cd8a3531))
* remove empty announce bar when not on release channel ([#224](https://github.com/ff-fab/cosalette/issues/224)) ([ef6ab74](https://github.com/ff-fab/cosalette/commit/ef6ab7481d6bb0197c25a46122fa26b5c48fa466))
* replace deprecated macos-13 runner with macos-14 cross-compile ([#108](https://github.com/ff-fab/cosalette/issues/108)) ([7ddfdc5](https://github.com/ff-fab/cosalette/commit/7ddfdc536355b20e8d88f9904cd13d49bb59c206))
* route BuildKit cache to :buildcache tag, not :latest ([#264](https://github.com/ff-fab/cosalette/issues/264)) ([9b1adcf](https://github.com/ff-fab/cosalette/commit/9b1adcf5f8f801242f5072e30401f802b6a03496))
* scaffold templates generate idiomatic decorator-based apps ([a6dc729](https://github.com/ff-fab/cosalette/commit/a6dc7298c2b9042c293b1238cd0301f36d962eb0))
* set #[pyclass(module)] on all pyo3 filter classes ([#99](https://github.com/ff-fab/cosalette/issues/99)) ([0461c9f](https://github.com/ff-fab/cosalette/commit/0461c9fc72a5def158a5bb9470f33517de4452d1))
* set docs_channel default to dev (was incorrectly committed as release) ([8eb4ebe](https://github.com/ff-fab/cosalette/commit/8eb4ebec005656f5a1a3184f37110cf0e12eadc9))
* **skills:** avoid heredoc in PR creation to prevent spinner hang ([#216](https://github.com/ff-fab/cosalette/issues/216)) ([65cfefd](https://github.com/ff-fab/cosalette/commit/65cfefd0eeea2f4d9606e733771a9d6000efd14f))
* suppress devcontainer image push in docs build jobs ([#213](https://github.com/ff-fab/cosalette/issues/213)) ([bd51652](https://github.com/ff-fab/cosalette/commit/bd51652b8ea5bbe0c8ba160761303ac4badaf0f8))
* switch test report link from gistpreview to gist.githack.com ([a8a76ef](https://github.com/ff-fab/cosalette/commit/a8a76ef77c5a9c78983457be9d1d539016dbf55b))
* temporarily make TestPyPI non-blocking for v0.2.0 publish ([724d90f](https://github.com/ff-fab/cosalette/commit/724d90f8d533192ca0adf28e83a791872fa7c058))
* **types:** resolve ty errors for Python 3.14 compatibility ([#244](https://github.com/ff-fab/cosalette/issues/244)) ([19c3bdc](https://github.com/ff-fab/cosalette/commit/19c3bdc0f20c5dcad2ad9c6e0c111f4b2ece7d34))
* update README badges and remove broken beads merge driver ([#199](https://github.com/ff-fab/cosalette/issues/199)) ([236dfb4](https://github.com/ff-fab/cosalette/commit/236dfb4852aba145c54e981815eedae112949bec))
* upgrade anchore/sbom-action v0.9.0 → v0.24.0 ([#266](https://github.com/ff-fab/cosalette/issues/266)) ([f7fd0b3](https://github.com/ff-fab/cosalette/commit/f7fd0b37708c6197fbe12a976c6e5645eab8b1d4))
* use draft releases to attach SBOM before immutability lock ([#203](https://github.com/ff-fab/cosalette/issues/203)) ([b9cac8a](https://github.com/ff-fab/cosalette/commit/b9cac8a68bec449669b6bb8fc8b27c6d102a10f8))
* use jq --rawfile for gist update and update security policy ([c87b712](https://github.com/ff-fab/cosalette/commit/c87b712dc8be86a9556a2f6875cf3d2cd7d1f4cf))
* use PEP 639 SPDX license expression for Metadata 2.4 compatibility ([#113](https://github.com/ff-fab/cosalette/issues/113)) ([6fa2180](https://github.com/ff-fab/cosalette/commit/6fa2180cfde6c439fe888aef55b7121fb04ab940))
* widen name= to str | NameSpec | None across all three decorator methods ([#240](https://github.com/ff-fab/cosalette/issues/240)) ([df57754](https://github.com/ff-fab/cosalette/commit/df577549a1f138a40b171a7dad8299943731c36d))


### Code Refactoring

* **stream:** consolidate async-only StreamablePort, remove AsyncStreamablePort ([#284](https://github.com/ff-fab/cosalette/issues/284)) ([b4f8c93](https://github.com/ff-fab/cosalette/commit/b4f8c931026faea9e6bf27e2b5aaeb2ba58e352d))

## [0.4.1](https://github.com/ff-fab/cosalette/compare/v0.4.0...v0.4.1) (2026-06-21)


### Bug Fixes

* **deps:** bump idna 3.17 and pymdown-extensions 10.21.3 (security) ([#297](https://github.com/ff-fab/cosalette/issues/297)) ([bc32704](https://github.com/ff-fab/cosalette/commit/bc327046257506d8357d80564eb96c23b953c6b1))
* **deps:** fix all security vulnerabilities to unblock dependabot PRs ([#306](https://github.com/ff-fab/cosalette/issues/306)) ([138bb92](https://github.com/ff-fab/cosalette/commit/138bb929f13b7fcaf0ff9aa9ddebc1186293fc72))

## [0.4.0](https://github.com/ff-fab/cosalette/compare/v0.3.13...v0.4.0) (2026-05-12)


### ⚠ BREAKING CHANGES

* **stream:** AsyncStreamablePort[T] is removed. All stream adapters must now implement the single StreamablePort[T] protocol with async lifecycle methods (async def open/close/start_scan/stop_scan). The registration key is StreamablePort[T] in all cases.

### Features

* add canonical AsyncAPI introspection ([#286](https://github.com/ff-fab/cosalette/issues/286)) ([38c12b2](https://github.com/ff-fab/cosalette/commit/38c12b2f843cd38bf68e01f0edf6b99991a57f99))
* add state reactors ([#279](https://github.com/ff-fab/cosalette/issues/279)) ([cce9072](https://github.com/ff-fab/cosalette/commit/cce9072e56f24b18a58f3f9491c098d1ec7472e2))
* add typed handler contracts ([#285](https://github.com/ff-fab/cosalette/issues/285)) ([1e6e0be](https://github.com/ff-fab/cosalette/commit/1e6e0bee5fb4e0f402447ec8203ebc40ba6858e8))
* cos-qn1 · cos-089 · cos-9et · cos-hld ([#288](https://github.com/ff-fab/cosalette/issues/288)) ([9eba0de](https://github.com/ff-fab/cosalette/commit/9eba0de998372ec8d2d7a367e02aeba583aa0d5a))
* **router:** add public router composition ([e519398](https://github.com/ff-fab/cosalette/commit/e5193988d5ca568ccbd349d30beaf4d4d2db2398))
* **stream:** stateful stream receiver runtime parity ([#283](https://github.com/ff-fab/cosalette/issues/283)) ([380a59e](https://github.com/ff-fab/cosalette/commit/380a59ea5c80dbdaa85849b1bdc2b8f2383ab46c))
* **testing:** add harness helpers and docs ([#287](https://github.com/ff-fab/cosalette/issues/287)) ([4e62420](https://github.com/ff-fab/cosalette/commit/4e624200c4d9fc4ab815a092192c18c17e414948))


### Code Refactoring

* **stream:** consolidate async-only StreamablePort, remove AsyncStreamablePort ([#284](https://github.com/ff-fab/cosalette/issues/284)) ([b4f8c93](https://github.com/ff-fab/cosalette/commit/b4f8c931026faea9e6bf27e2b5aaeb2ba58e352d))

## [0.3.13](https://github.com/ff-fab/cosalette/compare/v0.3.12...v0.3.13) (2026-05-03)


### Features

* add [@app](https://github.com/app).state lifespan-scoped shared-state factory ([#247](https://github.com/ff-fab/cosalette/issues/247)) ([bf15d61](https://github.com/ff-fab/cosalette/commit/bf15d61c8d5eae2e4f8abf58ad67c3048d5bff9a))
* add opencode.ai and kilo.ai support to cosalette ai init ([#258](https://github.com/ff-fab/cosalette/issues/258)) ([d5e9398](https://github.com/ff-fab/cosalette/commit/d5e9398069f03cade31fbfe46d405d01993f1951))
* **app:** add [@app](https://github.com/app).periodic background interval tasks ([#250](https://github.com/ff-fab/cosalette/issues/250)) ([0f591a0](https://github.com/ff-fab/cosalette/commit/0f591a086b6b7d479a5b7325971231cd542067aa))
* **commands:** add sub-dispatch routing (sub=, sub_key=) ([#249](https://github.com/ff-fab/cosalette/issues/249)) ([5021ce7](https://github.com/ff-fab/cosalette/commit/5021ce728d73125d3a5d14ef004f29485a59615d))
* **cos-1kl:** enable mode=max BuildKit registry cache ([#261](https://github.com/ff-fab/cosalette/issues/261)) ([d48b980](https://github.com/ff-fab/cosalette/commit/d48b980171747da43da8a18df382e71cd9209acd))
* **cos-6c3:** move Rust to Dockerfile for BuildKit layer caching ([#260](https://github.com/ff-fab/cosalette/issues/260)) ([598154c](https://github.com/ff-fab/cosalette/commit/598154c4ee9d3e484cfd18bab74aae4be9a4407b))
* **cos-762:** move docker-in-docker from feature to Dockerfile ([#262](https://github.com/ff-fab/cosalette/issues/262)) ([f5ed9c2](https://github.com/ff-fab/cosalette/commit/f5ed9c218f2c3469c887346e977929eecc997129))
* **devcontainer:** add OCI image description labels ([#267](https://github.com/ff-fab/cosalette/issues/267)) ([79c1467](https://github.com/ff-fab/cosalette/commit/79c1467abdb080d1d5c67cd7620c7a4316640c4d))
* Group C follow-ups — type widening, AsyncExitStack, bounded queue ([#253](https://github.com/ff-fab/cosalette/issues/253)) ([b675739](https://github.com/ff-fab/cosalette/commit/b675739857254068e8336d8a0efb72eedddc8fc6))
* harden security posture ([#275](https://github.com/ff-fab/cosalette/issues/275)) ([856ecde](https://github.com/ff-fab/cosalette/commit/856ecdeb8040db110568eb51d14e5befd98a33be))
* implement [@app](https://github.com/app).stream decorator with full lifecycle integration ([#252](https://github.com/ff-fab/cosalette/issues/252)) ([0eaa52e](https://github.com/ff-fab/cosalette/commit/0eaa52e236bb6dc31b9ce7ff1e7dcd9c21b626b8))
* **stream:** add StreamablePort and Stream async bridge ([#251](https://github.com/ff-fab/cosalette/issues/251)) ([4aca615](https://github.com/ff-fab/cosalette/commit/4aca615e8b7225b24b27c1808dd7364fecd3c6bf))


### Bug Fixes

* add docker-container buildx driver to devcontainer CI jobs ([#263](https://github.com/ff-fab/cosalette/issues/263)) ([96a6db9](https://github.com/ff-fab/cosalette/commit/96a6db9c8491432d423d11f14abfb15aaef437e5))
* **ci:** address docs workflow review findings ([#278](https://github.com/ff-fab/cosalette/issues/278)) ([63151a3](https://github.com/ff-fab/cosalette/commit/63151a3bcb783f457acf6d37528b41821d578166))
* **ci:** skip docs build for non-docs PRs and release-please ([#277](https://github.com/ff-fab/cosalette/issues/277)) ([6b33cc0](https://github.com/ff-fab/cosalette/commit/6b33cc08555f559ab815058c69d04c60016f3565))
* **ci:** skip heavy jobs for release-please PRs ([#276](https://github.com/ff-fab/cosalette/issues/276)) ([6cb2e3d](https://github.com/ff-fab/cosalette/commit/6cb2e3d5cd64350bba3373e61aa525542389308e))
* push devcontainer image explicitly, not via devcontainers/ci ([#265](https://github.com/ff-fab/cosalette/issues/265)) ([8a8900d](https://github.com/ff-fab/cosalette/commit/8a8900daaf053fd731405aa7b61f85f0c989e43c))
* route BuildKit cache to :buildcache tag, not :latest ([#264](https://github.com/ff-fab/cosalette/issues/264)) ([9b1adcf](https://github.com/ff-fab/cosalette/commit/9b1adcf5f8f801242f5072e30401f802b6a03496))
* upgrade anchore/sbom-action v0.9.0 → v0.24.0 ([#266](https://github.com/ff-fab/cosalette/issues/266)) ([f7fd0b3](https://github.com/ff-fab/cosalette/commit/f7fd0b37708c6197fbe12a976c6e5645eab8b1d4))

## [0.3.12](https://github.com/ff-fab/cosalette/compare/v0.3.11...v0.3.12) (2026-04-25)


### Bug Fixes

* complete partial-handler coverage across introspect, injection, utils ([#243](https://github.com/ff-fab/cosalette/issues/243)) ([96521aa](https://github.com/ff-fab/cosalette/commit/96521aa13094fcb1d872db46dc940365361b7b31))
* **types:** resolve ty errors for Python 3.14 compatibility ([#244](https://github.com/ff-fab/cosalette/issues/244)) ([19c3bdc](https://github.com/ff-fab/cosalette/commit/19c3bdc0f20c5dcad2ad9c6e0c111f4b2ece7d34))

## [0.3.11](https://github.com/ff-fab/cosalette/compare/v0.3.10...v0.3.11) (2026-04-24)


### Bug Fixes

* widen name= to str | NameSpec | None across all three decorator methods ([#240](https://github.com/ff-fab/cosalette/issues/240)) ([df57754](https://github.com/ff-fab/cosalette/commit/df577549a1f138a40b171a7dad8299943731c36d))

## [0.3.10](https://github.com/ff-fab/cosalette/compare/v0.3.9...v0.3.10) (2026-04-24)


### Features

* per-device callable schedule= for name=callable telemetry ([#237](https://github.com/ff-fab/cosalette/issues/237)) ([a29d303](https://github.com/ff-fab/cosalette/commit/a29d303f4018ba242ebaaf96030f4b3ae7a175cd))

## [0.3.9](https://github.com/ff-fab/cosalette/compare/v0.3.8...v0.3.9) (2026-04-24)


### Features

* **telemetry:** allow triggerable=True with callable name= ([#236](https://github.com/ff-fab/cosalette/issues/236)) ([686131e](https://github.com/ff-fab/cosalette/commit/686131e7f16c05eccdc23164fe3b0b8e0936c26e))


### Bug Fixes

* **docs:** use light banner as PyPI fallback hero image ([#234](https://github.com/ff-fab/cosalette/issues/234)) ([a6afe02](https://github.com/ff-fab/cosalette/commit/a6afe0250ce10f20e9e05613ca72ddaca72b2b56))

## [0.3.8](https://github.com/ff-fab/cosalette/compare/v0.3.7...v0.3.8) (2026-04-23)


### Bug Fixes

* **docs:** extend contract-first guide to cover [@app](https://github.com/app).device metadata ([#233](https://github.com/ff-fab/cosalette/issues/233)) ([cf0c186](https://github.com/ff-fab/cosalette/commit/cf0c1861b7031e95e9a81f7d4419bbf65ba1b98d))
* **docs:** use absolute URLs for hero image in README ([#231](https://github.com/ff-fab/cosalette/issues/231)) ([bcebf89](https://github.com/ff-fab/cosalette/commit/bcebf896313fcd911335fbf6fdcab297c049a807))

## [0.3.7](https://github.com/ff-fab/cosalette/compare/v0.3.6...v0.3.7) (2026-04-23)


### Features

* contract metadata on [@app](https://github.com/app).device() and add_device() ([#227](https://github.com/ff-fab/cosalette/issues/227)) ([d37c222](https://github.com/ff-fab/cosalette/commit/d37c222031d7a7d814f82abfb41becaab811fc74))

## [0.3.6](https://github.com/ff-fab/cosalette/compare/v0.3.5...v0.3.6) (2026-04-23)


### Features

* contract-first MQTT framework enhancement (E1-E5) ([#225](https://github.com/ff-fab/cosalette/issues/225)) ([027c1ca](https://github.com/ff-fab/cosalette/commit/027c1ca52094bb75a70be0ef24c703880bc89af9))

## [0.3.5](https://github.com/ff-fab/cosalette/compare/v0.3.4...v0.3.5) (2026-04-20)


### Features

* lazy store resolution (FEP-001) ([#222](https://github.com/ff-fab/cosalette/issues/222)) ([842738e](https://github.com/ff-fab/cosalette/commit/842738ec5c215bc090c0551c34876b81c945d453))


### Bug Fixes

* remove empty announce bar when not on release channel ([#224](https://github.com/ff-fab/cosalette/issues/224)) ([ef6ab74](https://github.com/ff-fab/cosalette/commit/ef6ab7481d6bb0197c25a46122fa26b5c48fa466))

## [0.3.4](https://github.com/ff-fab/cosalette/compare/v0.3.3...v0.3.4) (2026-04-20)


### Features

* **ai:** promote multi-device name=callable to all AI surfaces ([#219](https://github.com/ff-fab/cosalette/issues/219)) ([8d1c2e0](https://github.com/ff-fab/cosalette/commit/8d1c2e0a866dec41f4ab658fd3766badcc1ee6aa))


### Bug Fixes

* **ci:** replace broken replace() in workflow URLs ([#220](https://github.com/ff-fab/cosalette/issues/220)) ([680d00a](https://github.com/ff-fab/cosalette/commit/680d00ae2e820b15e59c649b51b6183e7ba4f126))

## [0.3.3](https://github.com/ff-fab/cosalette/compare/v0.3.2...v0.3.3) (2026-04-19)


### Features

* add triggerable telemetry with TriggerPayload injectable ([#214](https://github.com/ff-fab/cosalette/issues/214)) ([c51efe2](https://github.com/ff-fab/cosalette/commit/c51efe25d5cbab4728b4cbe7270f627ed8d2a498))


### Bug Fixes

* **skills:** avoid heredoc in PR creation to prevent spinner hang ([#216](https://github.com/ff-fab/cosalette/issues/216)) ([65cfefd](https://github.com/ff-fab/cosalette/commit/65cfefd0eeea2f4d9606e733771a9d6000efd14f))
* suppress devcontainer image push in docs build jobs ([#213](https://github.com/ff-fab/cosalette/issues/213)) ([bd51652](https://github.com/ff-fab/cosalette/commit/bd51652b8ea5bbe0c8ba160761303ac4badaf0f8))

## [0.3.2](https://github.com/ff-fab/cosalette/compare/v0.3.1...v0.3.2) (2026-04-18)


### Features

* **ai:** day-1 DX improvements from v0.3.1 adoption ([#211](https://github.com/ff-fab/cosalette/issues/211)) ([9271b5b](https://github.com/ff-fab/cosalette/commit/9271b5bf837fc6aabde583351d9d2bce59c5f291))


### Bug Fixes

* checkout release commit by SHA instead of tag name ([#208](https://github.com/ff-fab/cosalette/issues/208)) ([b1b21cc](https://github.com/ff-fab/cosalette/commit/b1b21cc377a49aeeb33ea1d26fc674f00113615b))

## [0.3.1](https://github.com/ff-fab/cosalette/compare/v0.3.0...v0.3.1) (2026-04-17)


### Bug Fixes

* hide excluded directories from docs navigation ([#206](https://github.com/ff-fab/cosalette/issues/206)) ([fbba040](https://github.com/ff-fab/cosalette/commit/fbba040a23ff92c102e2b73deef49d6f8dfefa55))
* use draft releases to attach SBOM before immutability lock ([#203](https://github.com/ff-fab/cosalette/issues/203)) ([b9cac8a](https://github.com/ff-fab/cosalette/commit/b9cac8a68bec449669b6bb8fc8b27c6d102a10f8))

## [0.3.0](https://github.com/ff-fab/cosalette/compare/v0.2.1...v0.3.0) (2026-04-17)


### ⚠ BREAKING CHANGES

* add sub-topic routing for command handlers ([#131](https://github.com/ff-fab/cosalette/issues/131))

### Features

* add [@app](https://github.com/app).on_configure lifecycle hook and dict-name multi-device registration ([#124](https://github.com/ff-fab/cosalette/issues/124)) ([b4a95bf](https://github.com/ff-fab/cosalette/commit/b4a95bfb4416ba8babb712371afd333bd74bdc94))
* add adapter auto-restart on health check failure ([#143](https://github.com/ff-fab/cosalette/issues/143)) ([0a2093e](https://github.com/ff-fab/cosalette/commit/0a2093e9cc16bc98fac2cd0470f7400c77704fa9))
* add brand identity assets and wire into docs site ([#148](https://github.com/ff-fab/cosalette/issues/148)) ([a4ed50e](https://github.com/ff-fab/cosalette/commit/a4ed50e5568791d769ef97992b2a75854c89a6a4))
* add Command dataclass and per-device command queue ([#128](https://github.com/ff-fab/cosalette/issues/128)) ([fc0a4b4](https://github.com/ff-fab/cosalette/commit/fc0a4b4991802fdf1257471ee18c72ad069cacd1))
* add ctx.commands() async iterator for device loops ([#129](https://github.com/ff-fab/cosalette/issues/129)) ([37aef84](https://github.com/ff-fab/cosalette/commit/37aef8486c9892100cb0db590c74feb648617a45))
* add HealthCheckable protocol and adapter detection ([#136](https://github.com/ff-fab/cosalette/issues/136)) ([22e9a4a](https://github.com/ff-fab/cosalette/commit/22e9a4a41ea165b1b826a632e9c7306360b8d707))
* add hero banners, social preview, and brand asset scripts ([#150](https://github.com/ff-fab/cosalette/issues/150)) ([ab75abe](https://github.com/ff-fab/cosalette/commit/ab75abe63d75c97abe47cbe9ed43d58d02ec7352))
* add lifespan-yielded injectable state (ADR-027) ([#134](https://github.com/ff-fab/cosalette/issues/134)) ([877b1d6](https://github.com/ff-fab/cosalette/commit/877b1d6837ad1ec2170841a820a12719895876ed))
* add Open Graph meta tags, custom 404 page, and PyPI badge icon ([#152](https://github.com/ff-fab/cosalette/issues/152)) ([ea854bd](https://github.com/ff-fab/cosalette/commit/ea854bdf606ffc835a030f263a19acf3ebdb50c3))
* add periodic health check runner with availability toggling ([#137](https://github.com/ff-fab/cosalette/issues/137)) ([f175483](https://github.com/ff-fab/cosalette/commit/f175483c8558cdfbdcb2144b53c7337eb6b9cec1))
* add PR-specific banner to docs preview builds ([132eabd](https://github.com/ff-fab/cosalette/commit/132eabd016f6aba44b8bd79d6b571464384d6457))
* add schedule= parameter and ctx.sleep_until() ([94c7959](https://github.com/ff-fab/cosalette/commit/94c7959c761d71c9721007356ba3cc0e5cf30e02))
* add schedule= parameter and ctx.sleep_until() ([84a2169](https://github.com/ff-fab/cosalette/commit/84a21694c158495e5e82c1b74c99ee87f7f055c6))
* add schema data model and AsyncAPI 3.0.0 loader ([#174](https://github.com/ff-fab/cosalette/issues/174)) ([c5e9b20](https://github.com/ff-fab/cosalette/commit/c5e9b20d183e693480b93d1492a4ba6e8712123d))
* add sub-topic routing for command handlers ([#131](https://github.com/ff-fab/cosalette/issues/131)) ([06d320e](https://github.com/ff-fab/cosalette/commit/06d320ebf1396ae627ae21899b8348c76c10f75d))
* auto-publish registry snapshot and prune cancelled tasks ([#166](https://github.com/ff-fab/cosalette/issues/166)) ([58e56a8](https://github.com/ff-fab/cosalette/commit/58e56a88e9a476a94e6827bc32cadea47e369827))
* bind agent models and add docs-subagent ([#163](https://github.com/ff-fab/cosalette/issues/163)) ([c495e2e](https://github.com/ff-fab/cosalette/commit/c495e2e580eb3c570713c5c7e69a8820eb463b4d))
* brand assets round 2 — logotype, favicon bg, theme logo ([#149](https://github.com/ff-fab/cosalette/issues/149)) ([369108b](https://github.com/ff-fab/cosalette/commit/369108bbdafde728f9597e2087755e5ed58642f2))
* configurable retry/backoff on [@app](https://github.com/app).telemetry (ADR-024) ([#126](https://github.com/ff-fab/cosalette/issues/126)) ([3733577](https://github.com/ff-fab/cosalette/commit/37335770d8c8aa4b842166d34544c43aaf733876))
* docs feedback widget, centered badges, PR template & subagent ([#200](https://github.com/ff-fab/cosalette/issues/200)) ([1825341](https://github.com/ff-fab/cosalette/commit/182534128c86f2304b2b2e55931c0804f14d13e8))
* Epic 8 — dynamic sub-entity availability (ADR-031) ([#158](https://github.com/ff-fab/cosalette/issues/158)) ([6b65b54](https://github.com/ff-fab/cosalette/commit/6b65b54c0a29753bc75a96d7ab93f1946f675e32))
* hero responsive CSS and Mermaid brand color alignment ([#153](https://github.com/ff-fab/cosalette/issues/153)) ([5030e2c](https://github.com/ff-fab/cosalette/commit/5030e2c48fc3a0ab5b2ef559f546381dbc4e33a2))
* MCP scaffolding tools (F4) — completes cos-0e5 epic ([#190](https://github.com/ff-fab/cosalette/issues/190)) ([e384a83](https://github.com/ff-fab/cosalette/commit/e384a832a7a21b2f33848a9369e000e5dfa7a32f))
* optimize agent orchestration system ([#160](https://github.com/ff-fab/cosalette/issues/160)) ([03da8be](https://github.com/ff-fab/cosalette/commit/03da8be77b177dcc587324fc6da603612bb023fe))
* optional MCP server for downstream AI support (F1-F3, F5) ([#189](https://github.com/ff-fab/cosalette/issues/189)) ([a68b0a5](https://github.com/ff-fab/cosalette/commit/a68b0a57114d9b7c73d2023a732347e0cf38ebeb))
* schema CLI tooling (validate, check, dump, init, slice) ([#176](https://github.com/ff-fab/cosalette/issues/176)) ([4279655](https://github.com/ff-fab/cosalette/commit/42796555de9fbf0982cbfdc89a3e56fc3650f2b0))
* schema enforcement lifecycle integration + beads sync fix ([#175](https://github.com/ff-fab/cosalette/issues/175)) ([ae7cbe0](https://github.com/ff-fab/cosalette/commit/ae7cbe08a2511c387fe9d7f74c87545a17357488))
* schema enforcement Phase V — runtime validation and monitoring ([#182](https://github.com/ff-fab/cosalette/issues/182)) ([01740b9](https://github.com/ff-fab/cosalette/commit/01740b93d14351c0326a685106ef48e4da8de6ad))
* schema-driven ADR creation with JSON validation and rendering ([#168](https://github.com/ff-fab/cosalette/issues/168)) ([941e93f](https://github.com/ff-fab/cosalette/commit/941e93f965043793fbbd25e00eff6f27a4225b3f))
* update brand identity brief and add docs hero illustration ([#151](https://github.com/ff-fab/cosalette/issues/151)) ([f68e925](https://github.com/ff-fab/cosalette/commit/f68e9250eeba8c164fa499bca0ad8369471a6e99))
* wire commands() queue dispatch and add integration tests ([#130](https://github.com/ff-fab/cosalette/issues/130)) ([e543087](https://github.com/ff-fab/cosalette/commit/e5430874eefc68989ee3cfbcb1f212a08b242a74))


### Bug Fixes

* add continue-on-error to TestPyPI publish step ([78e81ef](https://github.com/ff-fab/cosalette/commit/78e81effc8e6a373c6374738faf7b488507a8587))
* add shutdown-event race guard to enter_restartable_adapters ([#144](https://github.com/ff-fab/cosalette/issues/144)) ([bbb4bcd](https://github.com/ff-fab/cosalette/commit/bbb4bcd4550e6e2c525b599768a69ab1a6cb890c))
* address review findings for schedule/cron feature ([f47c053](https://github.com/ff-fab/cosalette/commit/f47c053dd0f2f125dca227f3e16f9cc423cfc20d))
* allow docs-only PRs to pass required CI checks ([#122](https://github.com/ff-fab/cosalette/issues/122)) ([67e51c8](https://github.com/ff-fab/cosalette/commit/67e51c84066909326cf78843f185b4ec73e7cedb))
* avoid cancelling shared coalescing group task on single-adapter restart ([#146](https://github.com/ff-fab/cosalette/issues/146)) ([cdf9d2e](https://github.com/ff-fab/cosalette/commit/cdf9d2e66db977855977df434e2c1317bc762c22))
* collapse multiline SKILL.md descriptions and fix frontmatter typos ([6a06b63](https://github.com/ff-fab/cosalette/commit/6a06b635258de1645cc3a31fb90aff7b06cd7b57))
* collapse multiline SKILL.md descriptions and fix frontmatter typos ([32cc7d8](https://github.com/ff-fab/cosalette/commit/32cc7d8006268bedefa33a50d501c7dbb27f6ac7))
* improve commands() shutdown responsiveness for long timeouts ([#140](https://github.com/ff-fab/cosalette/issues/140)) ([1cbe466](https://github.com/ff-fab/cosalette/commit/1cbe4663777932dd93cfba4a8827b007d79b226e))
* pre-0.3.0 framework review — strengthen settings validation and close test gaps ([#169](https://github.com/ff-fab/cosalette/issues/169)) ([bc41886](https://github.com/ff-fab/cosalette/commit/bc41886af1601af7d158b15507affbbb17d95c97))
* prevent silent docs build failures ([#141](https://github.com/ff-fab/cosalette/issues/141)) ([f270896](https://github.com/ff-fab/cosalette/commit/f270896b695f580467f0b5a4f825069324fa9404))
* reduce _scaffold_device_impl cyclomatic complexity to B ([1f92071](https://github.com/ff-fab/cosalette/commit/1f92071249d16352ea8d8f87b524dd8c668f4d1c))
* scaffold templates generate idiomatic decorator-based apps ([a6dc729](https://github.com/ff-fab/cosalette/commit/a6dc7298c2b9042c293b1238cd0301f36d962eb0))
* set docs_channel default to dev (was incorrectly committed as release) ([8eb4ebe](https://github.com/ff-fab/cosalette/commit/8eb4ebec005656f5a1a3184f37110cf0e12eadc9))
* switch test report link from gistpreview to gist.githack.com ([a8a76ef](https://github.com/ff-fab/cosalette/commit/a8a76ef77c5a9c78983457be9d1d539016dbf55b))
* update README badges and remove broken beads merge driver ([#199](https://github.com/ff-fab/cosalette/issues/199)) ([236dfb4](https://github.com/ff-fab/cosalette/commit/236dfb4852aba145c54e981815eedae112949bec))
* use jq --rawfile for gist update and update security policy ([c87b712](https://github.com/ff-fab/cosalette/commit/c87b712dc8be86a9556a2f6875cf3d2cd7d1f4cf))

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
