"""Bootstrap wiring for cosalette applications.

Stateless functions that wire together settings, MQTT, services,
signal handlers, device contexts, routing, and the run-loop.
Originally private methods on :class:`~cosalette._app.App`; extracted
to shrink the god-class and turn ``_run_async`` into a clean recipe.

.. note::

   The module is private (``_wiring``), so the functions omit the
   leading underscore that they carried as ``App`` methods.
"""

# Re-export everything from sub-modules
from cosalette._wiring._bootstrap import (
    _build_configure_providers,
    resolve_settings,
    resolve_store_factory,
    run_configure_hooks,
)
from cosalette._wiring._context import (
    DeviceInfo,
    TriggerConfig,
    _partition_commands,
    _register_trigger_proxy,
    _register_triggerable_telemetry,
    build_adapter_device_map,
    build_contexts,
    build_stream_contexts,
    subscribe_and_connect,
    wire_router,
)
from cosalette._wiring._discovery import (
    DiscoveryConfig,
    build_discovery_payloads,
    publish_discovery,
    reconcile_discovery_topics,
)
from cosalette._wiring._infra import (
    _REGISTRY_PAYLOAD_WARN_BYTES,
    _enter_one_state,
    create_mqtt,
    create_services,
    enter_state_factories,
    install_signal_handlers,
    publish_device_availability,
    publish_registry_snapshot,
    publish_startup_snapshot,
    register_connect_reannounce,
)
from cosalette._wiring._resolution import (
    _DEFAULT_TIMEOUT_FACTOR,
    _enabled_arg,
    _reject_async_enabled,
    _resolve_list_enabled,
    _validate_enabled_telemetry,
    resolve_command_timeouts,
    resolve_enabled,
    resolve_intervals,
    resolve_intervals_periodic,
    resolve_timeouts,
    resolve_timeouts_periodic,
)
from cosalette._wiring._resolution_checks import (
    _check_command_registrations,
    _check_expanded_duplicates,
    _check_is_root_consistency,
    _check_regular_command_entry,
    _check_sub_dispatch_entry,
    _evaluate_name_spec,
    _expand_command_names,
    _expand_device_names,
    _expand_telemetry_names,
    _resolve_per_device_interval,
    _resolve_per_device_schedule,
    _resolve_per_device_timeout,
    _validate_config_type,
    expand_name_specs,
)
from cosalette._wiring._retained_cleanup import (
    build_entity_snapshot,
    reconcile_retained_topics,
)
from cosalette._wiring._task_lifecycle import (
    DeviceTaskMap,
    _build_periodic_providers,
    _cancel_phase_tasks,
    _exit_restartable_adapters,
    _expand_group_members,
    _is_shared_task,
    _start_telemetry_tasks,
    _validate_lifespan_state,
    cancel_periodic_tasks,
    cancel_tasks,
    cancel_tasks_for_adapter,
    heartbeat_loop,
    start_device_tasks_for_names,
    start_health_check_task,
    start_heartbeat_task,
    start_periodic_tasks,
    start_stream_tasks,
    wire_restart_callback,
)
from cosalette._wiring._tasks import (
    run_lifespan_and_devices,
    start_device_tasks,
)

__all__ = [
    # Constants
    "_REGISTRY_PAYLOAD_WARN_BYTES",
    "DeviceTaskMap",
    # Bootstrap
    "_build_configure_providers",
    "resolve_settings",
    "resolve_store_factory",
    "run_configure_hooks",
    # Resolution
    "_check_command_registrations",
    "_check_expanded_duplicates",
    "_check_is_root_consistency",
    "_check_regular_command_entry",
    "_check_sub_dispatch_entry",
    "_DEFAULT_TIMEOUT_FACTOR",
    "_enabled_arg",
    "_evaluate_name_spec",
    "_expand_command_names",
    "_expand_device_names",
    "_expand_telemetry_names",
    "_reject_async_enabled",
    "_resolve_list_enabled",
    "_resolve_per_device_interval",
    "_resolve_per_device_schedule",
    "_resolve_per_device_timeout",
    "_validate_config_type",
    "_validate_enabled_telemetry",
    "expand_name_specs",
    "resolve_enabled",
    "resolve_intervals",
    "resolve_intervals_periodic",
    "resolve_command_timeouts",
    "resolve_timeouts",
    "resolve_timeouts_periodic",
    # Infrastructure
    "_enter_one_state",
    "create_mqtt",
    "create_services",
    "enter_state_factories",
    "install_signal_handlers",
    "publish_device_availability",
    "publish_registry_snapshot",
    "publish_startup_snapshot",
    "register_connect_reannounce",
    # Retained cleanup
    "build_entity_snapshot",
    "reconcile_retained_topics",
    # Discovery (F23)
    "DiscoveryConfig",
    "build_discovery_payloads",
    "publish_discovery",
    "reconcile_discovery_topics",
    # Context
    "DeviceInfo",
    "TriggerConfig",
    "_partition_commands",
    "_register_trigger_proxy",
    "_register_triggerable_telemetry",
    "build_adapter_device_map",
    "build_contexts",
    "build_stream_contexts",
    "subscribe_and_connect",
    "wire_router",
    # Tasks
    "_build_periodic_providers",
    "_cancel_phase_tasks",
    "_exit_restartable_adapters",
    "_expand_group_members",
    "_is_shared_task",
    "_validate_lifespan_state",
    "cancel_periodic_tasks",
    "cancel_tasks",
    "cancel_tasks_for_adapter",
    "heartbeat_loop",
    "run_lifespan_and_devices",
    "start_device_tasks",
    "start_device_tasks_for_names",
    "start_health_check_task",
    "start_heartbeat_task",
    "start_periodic_tasks",
    "start_stream_tasks",
    "wire_restart_callback",
]
