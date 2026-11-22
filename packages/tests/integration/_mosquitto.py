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
MOSQUITTO_IMAGE = "eclipse-mosquitto:2.1.4-alpine@sha256:fa12172a051310efe5bcb7a6a11b77bd5faacb82cbacd7742aa758bf024fd8e4"  # noqa: E501

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
        try:
            wait_for_logs(self, r"mosquitto version \d+.\d+.\d+ running", timeout=30)
        except TimeoutError as exc:  # TEMP diagnostics (cos-8nmq), remove
            exc.add_note(_diagnose(self))
            raise
        return self

    @override
    def _configure(self) -> None:
        super()._configure()
        if self._host_port is not None:
            self.with_bind_ports(self.MQTT_PORT, self._host_port)


_DIAG_SCRIPT = r"""
set -x
id; cat /proc/self/uid_map /proc/self/gid_map
grep -E 'Cap(Eff|Bnd)' /proc/self/status
cat /proc/self/attr/current 2>&1
ls -lnd / /mosquitto /mosquitto/config /mosquitto/config/* 2>&1
stat /mosquitto/config/mosquitto.conf 2>&1
cat /mosquitto/config/mosquitto.conf 2>&1 | head -2
su -s /bin/sh mosquitto -c 'id; cat /mosquitto/config/mosquitto.conf | head -2' 2>&1
mount | grep -E ' / | /mosquitto' 2>&1
timeout 3 mosquitto -v -c /mosquitto/config/mosquitto.conf 2>&1 | head -5
echo DIAG-END
"""


def _diagnose(broker: MosquittoBroker) -> str:  # TEMP diagnostics (cos-8nmq)
    import time

    lines: list[str] = []
    try:
        client = broker.get_docker_client().client
        info = client.info()
        lines.append(
            f"daemon: driver={info.get('Driver')} "
            f"security={info.get('SecurityOptions')} "
            f"kernel={info.get('KernelVersion')}"
        )
        wrapped = broker.get_wrapped_container()
        wrapped.reload()
        lines.append(f"broker logs: {wrapped.logs().decode(errors='replace')}")
        lines.append(f"broker mounts: {(wrapped.attrs or {}).get('Mounts')}")
        diag = DockerContainer(MOSQUITTO_IMAGE)
        diag.with_kwargs(entrypoint=["sh", "-c", _DIAG_SCRIPT])
        diag.with_copy_into_container(MOSQUITTO_CONF, _CONTAINER_CONFIG_PATH)
        diag.start()
        for _ in range(30):
            out = diag.get_wrapped_container().logs().decode(errors="replace")
            if "DIAG-END" in out:
                break
            time.sleep(0.5)
        lines.append(f"diag container output:\n{out}")
        diag.stop()
    except Exception as e:  # noqa: BLE001
        lines.append(f"diagnostics failed: {e!r}")
    return "\n".join(lines)
