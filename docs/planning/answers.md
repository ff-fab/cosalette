# Initial project configuration

Project: velux2mqtt
HW Interface: GPIO (RPi.GPIO)
Protocol/Library: Relay pulses
Data Direction: Bidirectional (command + state)
Complexity Estimate: Medium
Legacy project as input: this one
Description: control of Velux window (incld. blinds) via MQTT

Project: concept2mqtt
HW Interface: BLE
Protocol/Library: have to research, some concept2 projects are existing, possibly also some SDK. Examples: https://github.com/janick/pROWess, https://github.com/OpenRowingCommunity/c2bluetooth
Data Direction: Bidirectional (command + state)
Complexity Estimate: Medium (high in research and development, probably not in the actual implementation)
Legacy project as input: none
Description: Start trainings programs on an concept2 rower and read in training data

Project: Smart lights
HW Interface: WiFi
Protocol/Library: pywizlight (https://github.com/sbidy/pywizlight)
Data Direction: Bidirectional (command + state)
Complexity Estimate: Medium
Legacy project as input: docs/planning/wizcontrol.py
Description: Control smart light bulbs via MQTT

Project: airthings2mqtt
HW Interface: BLE
Protocol/Library: https://github.com/Airthings/wave-reader
Data Direction: Unidirectional (state)
Complexity Estimate: Low
Legacy project as input: will be provided on project start
Description: Read in radon measurement values of Airthings Wave sensors

Project: wallpanel2mqtt
HW Interface: LAN
Protocol/Library: commands via SSH to the wallpanel computer
Data Direction: Bidirectional (command + state)
Complexity Estimate: Low
Legacy project as input: will be provided on project start
Description: Control of a wallpanel computer (hibernate, sleep, screen brightness)

Project: smartmeter2mqtt
HW Interface: IR head via USB
Protocol/Library: USB via FDTI
Data Direction: Bidirectional (command + state)
Complexity Estimate: High
Legacy project as input: https://github.com/volkszaehler/vzlogger
Description: read in (and initial config) of smartmeters

Project: vito2mqtt
HW Interface: Optolink to USB
Protocol/Library: USB, vcontrold
Data Direction: Bidirectional (command + state)
Complexity Estimate: High
Legacy project as input: https://github.com/openv/vcontrold (narrowed down to a specific configuration that will be provided at project start)
Description: control of a VitoDens domestic gas heater

Project: gas2mqtt
HW Interface: HMC5883 via GPIO
Protocol/Library: smbus
Data Direction: Unidirectional (state)
Complexity Estimate: medium
Legacy project as input: docs/planning/hmc5883.py
Description: read in values from a domestic gas counter


Most projects require multiple devices of same type. Most projects need some (but little) statefulness.
Data direction is mostly bidirectional, except for the ones that only read in sensor data.

HTTP/REST APIs are generally not involved, the only exception being the wallpanel2mqtt project which will use SSH commands. But it could also reuse the SSH client from the OS it is running on.

It might be noteworthy that the apps themselves are deployed on multiple different raspberry pi's and shall all be standalone apps.

# Architecture fit assessment

I agree with your split, but want to revisit the CommandHandler topic - I might benefit from some standardization on the topic convention and could see moving this to highly reusable code in the application layer (see below).

## Topic Convention

Yes, I want the topic layout standardised, following idiomatic and best practice approaches.

## Application layer patterns

Provide both patterns. Most projects are bidirectional to at least some extent.

## Framework shape

Framework clearly resonates the most with me. It just "feels" right.

### Framework opinions

Highly opinionated, that is why we are creating our own framework after all. Keep maintenance at a minimum, enforce clear conventions, and provide a solid foundation for all projects to build on. Some escape hatches are possible to allow for the flexibility needed by the initial project set.

### Entry Point vision

Gut feeling: framework heavy, draw inspiration from FastAPI's approach which highly appeals to me.
Is the hexagonal architecture pattern compatible with a FastAPI-like framework approach?

## Packaging & distribution

PyPI or git dependency.

PyPI sounds the cleanest, is there any reason not to use PyPI? My work is open source, and I don't mind sharing, but I focus on my private projects and don't want to "spam" any public catalog. For learning purposes however I would appreciate to use PyPI and I would take over at least some maintenance of the package.

### Versioning

Independent semver versioning of framework and projects. I can update everything at once though, but want to try to avoid that.

### Monorepo vs. multiple repos

Multi-repo it is.

## Deployment and runtime

### Deployment targets

All apps must be deployable as standalone apps on Raspberry Pi OS. Docker is not a requirement, but is nice to have for deployment as well.

In the overall project, I use ansible for deployment and usually docker-compose, but this shall not (at least not strongly) influence the design of the individual apps.

There is a shared MQTT broker which is not part of this project.

### Shared infrastructure

All projects share same credentials and MQTT broker, but the individual deployment to different hardware make individual configurations necessary.

In a later project I want to collect the logging information from all apps in a central place, no decision on tooling has been made yet though. Preparation for this is appreciated. Same is true for monitoring/alerting.

env file or config file approach shall be uniform, but config lives with each single app (e.g. env in a dockerized deployment).

Standardization for broker config, logging format, health reporting is requested as much as possible.

### Resource constraints

All devices are Raspberry Pi 4 or better or Pi Zero 2, resource constraints have not been a major concern in past, but resources are not unlimited.

## Developer Experience

copier sets up IDE settings and pre-commit hooks, no project structure at all.

The DevEx with this is nice and I will reuse it, but don't want any libraries or framework parts be part of the template, as I want to be able to use the same template for projects that do not use the framework as well and allow for good updatability both of the framework as well as the template itself.

### Testing Patterns

The framework shall provide:

- **Test fixture factories** (e.g., `make_mqtt_mock()`, `make_clock_fake()`)
- **A standard test harness** for integration tests (spin up service, send MQTT, assert)
- **pytest plugins** with shared fixtures

### CLI & Tooling

Each project should have a consistent CLI (`myapp --dry-run`, `myapp --version`).

The framework should provide CLI scaffolding (argparse/click/typer).

## Naming and identity

The framework shall be called `sh4`.

All projects shall be named like `velux2mqtt`, `airthings2mqtt`, `concept2mqtt`, …

### Configuration prefix

Configurable: framework accepts `env_prefix` as a parameter, defaulting to an empty string.

Projects can set this to their own prefix (e.g., `VELUX2MQTT_`) to avoid collisions, or
to an empty string for clear env when using env files in dockerized deployments.


## Migration and refactoring

We will not touch this app for now. Only work in dpcs/planning for now to lay out a
detailed plan.

We will then start a new project for the framework development, and only migrate this app to the framework once the framework is in a good shape and has the necessary features implemented. This way we can keep the migration effort manageable and focus on building the framework first.
