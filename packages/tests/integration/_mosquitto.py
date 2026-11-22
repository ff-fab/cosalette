"""Mosquitto test broker shared by the MQTT integration tests.

The broker config is copied into the created container before it starts,
never bind-mounted from the host.  Under rootless Docker (the CI
devcontainer) a bind-mounted pytest tmp file can be unreadable to the
broker, which then exits with "Unable to open config file" (cos-8nmq).
A file copied into the container's own filesystem is independent of host
paths, ownership and user-namespace mapping, and survives the
``kill`` + ``start`` cycle the reconnection tests use.
"""

from __future__ import annotations

from typing import Self, override

from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs
from testcontainers.mqtt import MosquittoContainer

# Pinned by tag and digest; renovate.json tracks both (keep on one line).
# Held on 2.0.x: 2.1.x cannot open its config on the kernel 7.0 CI runners
# (cos-8nmq). The 2.0.x images carry no -alpine suffix but are Alpine-based.
MOSQUITTO_IMAGE = "eclipse-mosquitto:2.0.23@sha256:6a332cc7a8dbe011cbc076180cbb76b37c6f0f55809682ea08e7980ef0112ad9"  # noqa: E501

MOSQUITTO_CONF = b"""\
listener 1883
protocol mqtt
allow_anonymous true
log_dest stdout
log_type error
log_type warning
log_type notice
log_type information
log_timestamp_format %Y-%m-%d %H:%M:%S
persistence false
"""

_CONTAINER_CONFIG_PATH = "/mosquitto/config/mosquitto.conf"


class MosquittoBroker(MosquittoContainer):
    """Mosquitto container with the test config copied in, not bind-mounted.

    Args:
        host_port: Pin the broker to this host port so it survives a Docker
            ``kill`` + ``start`` cycle (reconnection tests).  ``None`` keeps
            the default random host port.
    """

    def __init__(self, host_port: int | None = None) -> None:
        super().__init__(image=MOSQUITTO_IMAGE)
        self._host_port = host_port

    @override
    def start(self, configfile: str | None = None) -> Self:
        """Start the broker with :data:`MOSQUITTO_CONF`.

        Replaces ``MosquittoContainer.start``, which always bind-mounts a
        host config file.  Its ``_wait`` is also bypassed: it picks the
        Mosquitto 1.x code path from the text after the last ``:`` in the
        image name, which for a digest-pinned image is the digest.
        """
        if configfile is not None:
            msg = "MosquittoBroker always uses MOSQUITTO_CONF"
            raise ValueError(msg)
        self.with_exposed_ports(self.MQTT_PORT)
        self.with_tmpfs_mount("/data")
        self.with_copy_into_container(MOSQUITTO_CONF, _CONTAINER_CONFIG_PATH)
        DockerContainer.start(self)
        wait_for_logs(self, r"mosquitto version \d+.\d+.\d+ running", timeout=30)
        return self

    @override
    def _configure(self) -> None:
        super()._configure()
        if self._host_port is not None:
            self.with_bind_ports(self.MQTT_PORT, self._host_port)
