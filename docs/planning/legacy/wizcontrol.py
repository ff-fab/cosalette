import asyncio
import aiomqtt

import logging
import logging.handlers
import sys

import re

from pywizlight import wizlight, PilotBuilder, SCENES
from pywizlight.utils import hex_to_percent, percent_to_hex

from ast import literal_eval

# MQTT configuration
MQTT_SERVER = "pi4server.lan"
MQTT_PORT = 1883
MQTT_USER = "jl4"
MQTT_PW = "SKrSsZCyTgugYtYTu4Zv"

# logging
LOG_FILENAME = "/var/log/wizcontrol/wizcontrol.log"
LOG_LEVEL = logging.INFO


SCENE_NAME_TO_ID = {scene_name: scene_id for (scene_id, scene_name) in SCENES.items()}

TIMEOUT = 3

logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)


# make a handler that writes to a file, making a new file at midnight and
# keeping 3 backups
handler = logging.handlers.TimedRotatingFileHandler(
    LOG_FILENAME, when="midnight", backupCount=3
)
# format each log message like this
formatter = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")
handler.setFormatter(formatter)

# attach the handler to the logger
logger.addHandler(handler)


# make a class to capture stdout and sterr in the log
class MyLogger(object):
    def __init__(self, logger, level):
        self.logger = logger
        self.level = level

    def write(self, message):
        # Only log if there is a message (not just a new line)
        if message.rstrip() != "":
            self.logger.log(self.level, message.rstrip())

    def writelines(self, sequence):
        for message in sequence:
            self.write(message)

    def flush(self):
        pass

    def close(self):
        pass


# replace stdout with logging to file at INFO level
sys.stdout = MyLogger(logger, logging.INFO)
# replace stderr with logging to file at ERROR level
sys.stderr = MyLogger(logger, logging.ERROR)


async def get_state(light, timeout):
    get_state = asyncio.gather(*[(bulb.updateState()) for bulb in light])
    try:
        await asyncio.wait_for(get_state, timeout)
    except asyncio.TimeoutError:
        pass

    try:
        state = await get_state
    except asyncio.CancelledError:
        state = None

    return state


async def set_state(light, timeout, **kwargs):
    set_state = asyncio.gather(
        *[bulb.turn_on(PilotBuilder(**kwargs)) for bulb in light]
    )
    try:
        await asyncio.wait_for(set_state, timeout)
    except asyncio.TimeoutError:
        pass

    await set_state


async def turn_off(light):
    asyncio.gather(*[(bulb.turn_off()) for bulb in light])

    return


async def turn_on(light):
    set_state = asyncio.gather(*[bulb.turn_on() for bulb in light])
    try:
        await asyncio.wait_for(set_state, TIMEOUT)
    except asyncio.TimeoutError:
        pass

    await set_state


async def get_power(light):
    act_state = await get_state(light, TIMEOUT)

    if act_state is None:
        return "OFF"

    act = [state.get_state() for state in act_state]

    if len(set(act)) == 1:
        if act[0] is not None:
            return act[0]

    return "UNDEF"


async def set_scene(light, scene):
    try:
        await set_state(light, TIMEOUT, scene=SCENE_NAME_TO_ID[scene])
    except asyncio.CancelledError:
        return None

    # wait for the scene to change on all bulbs and report the new scene
    await asyncio.sleep(0.15)
    tries = 10
    while tries > 0:
        act_state = await get_state(light, TIMEOUT)
        if act_state is None:
            return None

        act = [state.get_scene() for state in act_state]
        if all(s == scene for s in act):
            return scene
        tries = tries - 1
        await asyncio.sleep(0.1)

    try:
        act_state = await asyncio.wait_for(light[0].updateState(), TIMEOUT)
        return act_state.get_scene()
    except asyncio.TimeoutError:
        return None


async def get_scene(light):
    act_state = await get_state(light, TIMEOUT)

    if act_state is None:
        return "NULL"

    act = [state.get_scene() for state in act_state]

    if len(set(act)) == 1:
        if act[0] is not None:
            return act[0]

    return "UNDEF"


async def set_brightness(light, dim):
    try:
        await set_state(light, TIMEOUT, brightness=percent_to_hex(dim))
    except asyncio.CancelledError:
        return None

    # wait for the dimming to change on all bulbs and report the new brightness
    await asyncio.sleep(0.15)
    tries = 10
    while tries > 0:
        act_state = await get_state(light, TIMEOUT)
        if act_state is None:
            return None

        act = [state.get_brightness() for state in act_state]
        if all(d == percent_to_hex(dim) for d in act):
            return dim
        tries = tries - 1
        await asyncio.sleep(0.1)

    try:
        act_state = await asyncio.wait_for(light[0].updateState(), TIMEOUT)
        return int(hex_to_percent(act_state.get_brightness()))
    except asyncio.TimeoutError:
        return None


async def get_brightness(light):
    act_state = await get_state(light, TIMEOUT)
    if act_state is None:
        return "NULL"

    act = [state.get_brightness() for state in act_state]

    if len(set(act)) == 1:
        if act[0] is not None:
            return hex_to_percent(act[0])

    return "UNDEF"


async def set_speed(light, speed):
    try:
        # speed must be set together with scene
        scene = await get_scene(light)
        await set_state(
            light,
            TIMEOUT,
            scene=SCENE_NAME_TO_ID[scene],
            speed=min(max(10, speed), 200),
        )
    except asyncio.CancelledError:
        return None

    # wait for the speed setting to change on all bulbs and report the new
    # setting
    await asyncio.sleep(0.15)
    tries = 10
    while tries > 0:
        act_state = await get_state(light, TIMEOUT)
        if act_state is None:
            return None

        act = [state.get_speed() for state in act_state]
        if all(s == speed for s in act):
            return speed
        tries = tries - 1
        await asyncio.sleep(0.1)

    try:
        act_state = await asyncio.wait_for(light[0].updateState(), TIMEOUT)
        return act_state.get_speed()
    except asyncio.TimeoutError:
        return None


async def get_speed(light):
    act_state = await get_state(light, TIMEOUT)
    if act_state is None:
        return "NULL"

    act = [state.get_speed() for state in act_state]

    if len(set(act)) == 1:
        return act[0]

    return "UNDEF"


async def set_rgb(light, r, g, b):
    try:
        await set_state(light, TIMEOUT, rgb=(r, g, b))
    except asyncio.CancelledError:
        return None

    # wait for the color setting to change on all bulbs and report the new
    # color values
    await asyncio.sleep(0.15)
    tries = 10
    while tries > 0:
        act_state = await get_state(light, TIMEOUT)
        if act_state is None:
            return None

        act = [state.get_rgb() for state in act_state]
        if all(c == (r, g, b) for c in act):
            return (r, g, b)
        tries = tries - 1
        await asyncio.sleep(0.1)

    try:
        act_state = await asyncio.wait_for(light[0].updateState(), TIMEOUT)
        return act_state.get_rgb()
    except asyncio.TimeoutError:
        return None


async def get_rgb(light):
    act_state = await get_state(light, TIMEOUT)
    if act_state is None:
        return "NULL"

    act = [state.get_rgb() for state in act_state]

    if len(set(act)) == 1:
        if act[0][0] is not None:
            return act[0]

    return "UNDEF"


async def set_temperature(light, kelvin):
    try:
        await set_state(light, TIMEOUT, colortemp=kelvin)
    except asyncio.CancelledError:
        return None

    # wait for the temperature to change on all bulbs and report the new
    # temperature
    await asyncio.sleep(0.15)
    tries = 10
    while tries > 0:
        act_state = await get_state(light, TIMEOUT)
        if act_state is None:
            return None

        act = [state.get_colortemp() for state in act_state]
        if all(k == kelvin for k in act):
            return kelvin
        tries = tries - 1
        await asyncio.sleep(0.1)

    try:
        act_state = await asyncio.wait_for(light[0].updateState(), TIMEOUT)
        return act_state.get_colortemp()
    except asyncio.TimeoutError:
        return None


async def get_temperature(light):
    act_state = await get_state(light, TIMEOUT)
    if act_state is None:
        return "NULL"

    act = [state.get_colortemp() for state in act_state]

    if len(set(act)) == 1:
        if act[0] is not None:
            return act[0]

    return "UNDEF"


async def evaluate_msg(topic, payload, light):

    response_topic = None
    response_payload = None

    # use a regular expression to return the string between "wiz/" and the next
    # following "/"
    lamp = re.match(r"wiz/(.*?)/", str(topic))
    if lamp is None:
        return [response_topic, response_payload]
    else:
        lamp = lamp.group(1)

    # turn on or off
    if str(topic) == f"wiz/{lamp}/power/set":
        cmd = payload.decode("UTF-8")
        if cmd.lower() == "on":
            await turn_on(light)
        elif cmd.lower() == "off":
            await turn_off(light)
        power = await get_power(light)
        response_topic = f"wiz/{lamp}/power/actual"
        response_payload = "ON" if power else "OFF"

    # get power state
    if str(topic) == f"wiz/{lamp}/power/get":
        power = await get_power(light)
        response_topic = f"wiz/{lamp}/power/actual"
        response_payload = "ON" if power else "OFF"

    # set scene
    if str(topic) == f"wiz/{lamp}/scene/set":
        scene = payload.decode("UTF-8")  # type: ignore
        if scene in SCENES.values():
            scene = await set_scene(light, scene)
            response_topic = f"wiz/{lamp}/scene/actual"
            response_payload = scene

    # get scene
    if str(topic) == f"wiz/{lamp}/scene/get":
        scene = await get_scene(light)
        response_topic = f"wiz/{lamp}/scene/actual"
        response_payload = scene

    # set brightness
    if str(topic) == f"wiz/{lamp}/brightness/set":
        dim = int(payload.decode("UTF-8"))  # type: ignore
        if dim >= 0 and dim <= 100:
            dim = await set_brightness(light, dim)
            response_topic = f"wiz/{lamp}/brightness/actual"
            response_payload = dim

    # get brightness
    if str(topic) == f"wiz/{lamp}/brightness/get":
        dim = await get_brightness(light)
        response_topic = f"wiz/{lamp}/brightness/actual"
        response_payload = dim

    # set speed
    if str(topic) == f"wiz/{lamp}/speed/set":
        speed = int(float(payload.decode("UTF-8")))  # type: ignore
        if speed >= 10 and speed <= 200:
            speed = await set_speed(light, speed)
            response_topic = f"wiz/{lamp}/speed/actual"
            response_payload = speed

    # get speed
    if str(topic) == f"wiz/{lamp}/speed/get":
        speed = await get_speed(light)
        response_topic = f"wiz/{lamp}/speed/actual"
        response_payload = speed

    # set r,g,b
    if str(topic) == f"wiz/{lamp}/rgb/set":
        rgb = literal_eval(payload.decode("UTF-8"))  # type: ignore
        rgb = await set_rgb(light, *rgb)
        response_topic = f"wiz/{lamp}/rgb/actual"
        response_payload = str(rgb)

    # get r,g,b
    if str(topic) == f"wiz/{lamp}/rgb/get":
        rgb = await get_rgb(light)
        response_topic = f"wiz/{lamp}/rgb/actual"
        response_payload = str(rgb)

    # set temperature
    if str(topic) == f"wiz/{lamp}/temperature/set":
        kelvin = int(payload.decode("UTF-8"))  # type: ignore
        if kelvin >= 2200 and kelvin <= 6500:
            kelvin = await set_temperature(light, kelvin)
            response_topic = f"wiz/{lamp}/temperature/actual"
            response_payload = kelvin

    # get temperature
    if str(topic) == f"wiz/{lamp}/temperature/get":
        kelvin = await get_temperature(light)
        response_topic = f"wiz/{lamp}/temperature/actual"
        response_payload = kelvin

    return [response_topic, response_payload]


async def main():
    lights = {
        "light_bedroom_ceiling": [
            wizlight("192.168.12.98"),
            wizlight("192.168.12.99"),
            wizlight("192.168.12.100"),
        ],
        "light_bedroom_ceiling_bulb1": [wizlight("192.168.12.98")],
        "light_bedroom_ceiling_bulb2": [wizlight("192.168.12.99")],
        "light_bedroom_ceiling_bulb3": [wizlight("192.168.12.100")],
        "light_toilet_basement": [
            wizlight("192.168.12.115"),
            wizlight("192.168.12.116"),
            wizlight("192.168.12.111"),
        ],
        "light_toilet_basement_mirror": [wizlight("192.168.12.114")],
        "light_julia_stripe": [wizlight("192.168.12.113")],
        "light_sauna_all": [
            wizlight("192.168.12.95"),
            wizlight("192.168.12.140"),
            wizlight("192.168.12.141"),
            wizlight("192.168.12.142"),
            wizlight("192.168.12.144"),
            wizlight("192.168.12.145"),
        ],
        "light_sauna_spot1": [wizlight("192.168.12.95")],
        "light_sauna_spot2": [wizlight("192.168.12.140")],
        "light_sauna_spot3": [wizlight("192.168.12.141")],
        "light_sauna_spot4": [wizlight("192.168.12.142")],
        "light_sauna_spot5": [wizlight("192.168.12.144")],
        "light_sauna_shower": [wizlight("192.168.12.145")],
        "light_sauna_without_shower": [
            wizlight("192.168.12.95"),
            wizlight("192.168.12.140"),
            wizlight("192.168.12.141"),
            wizlight("192.168.12.142"),
            wizlight("192.168.12.144"),
        ],
        "light_sauna_fun_spots": [
            wizlight("192.168.12.141"),
            wizlight("192.168.12.142"),
        ],
        "light_sauna_without_fun_and_shower": [
            wizlight("192.168.12.95"),
            wizlight("192.168.12.140"),
            wizlight("192.168.12.144"),
        ],
    }

    reconnect_interval = 3

    while True:
        try:
            async with aiomqtt.Client(
                MQTT_SERVER, MQTT_PORT, username=MQTT_USER, password=MQTT_PW
            ) as mqttc:
                await mqttc.subscribe("wiz/#")
                async for message in mqttc.messages:
                    if "actual" in str(message.topic):
                        continue

                    match = re.match(r"wiz/(.*?)/", str(message.topic))
                    if match is None:
                        continue

                    lamp = match.group(1)
                    light = lights.get(lamp)
                    if light is None:
                        continue

                    [topic, payload] = await evaluate_msg(
                        message.topic, message.payload, light
                    )
                    if topic is not None:
                        await mqttc.publish(topic, payload)

        except aiomqtt.MqttError as e:
            print(
                f"Connection lost, {e}; "
                f"Reconnecting in {reconnect_interval} seconds ..."
            )
            await asyncio.sleep(reconnect_interval)


asyncio.run(main())
