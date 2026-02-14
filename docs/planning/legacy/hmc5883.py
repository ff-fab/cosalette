#!/usr/bin/python -u
#
# hmc5883.py
#
# Program to read the gas counter value by using the digital magnetometer HMC5883

# Copyright 2014 Martin Kompf
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import smbus2 as smbus
import time
import argparse
import paho.mqtt.client as mqtt
import logging
import logging.handlers
import sys


# Logging
LOG_FILENAME = "/var/log/gas-counter/hmc5883.log"
LOG_LEVEL = logging.INFO

logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)

# Make a handler that writes to a file, making a new file at midnight
# and keeping 3 backups
handler = logging.handlers.TimedRotatingFileHandler(
    LOG_FILENAME, when="midnight", backupCount=3
)
# Format each log message like this
formatter = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")
handler.setFormatter(formatter)

# Attach the handler to the logger
logger.addHandler(handler)


# Make a class to capture stdout and sterr in the log
class MyLogger(object):
    def __init__(self, logger, level):
        """Needs a logger and a logger level."""
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


# Replace stdout with logging to file at INFO level
sys.stdout = MyLogger(logger, logging.INFO)
# Replace stderr with logging to file at ERROR level
sys.stderr = MyLogger(logger, logging.ERROR)


# Global data
# I2C bus (1 at newer Raspberry Pi, older models use 0)
bus = smbus.SMBus(1)
# I2C address of HMC5883
address = 0x0D

# Trigger level and hysteresis
TRIGGER_LEVEL = -5000
TRIGGER_HYST = 700


# MQTT Client
mqttc = mqtt.Client(client_id="piheat")
mqttc.username_pw_set("jl4", "SKrSsZCyTgugYtYTu4Zv")
mqttc.connected_flag = False
mqttc.max_inflight_messages_set(1000)


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.connected_flag = True  # set flag
    else:
        print("Bad connection, return code = ", rc)


mqttc.on_connect = on_connect


# Read block data from HMC5883
def read_data():
    return bus.read_i2c_block_data(address, 0x00, 9)


# Convert val to signed value
def twos_complement(val, len):
    if val & (1 << len - 1):
        val = val - (1 << len)
    return val


# Convert two bytes from data starting at offset to signed word
def convert_sw(data, offset):
    return twos_complement(data[offset + 1] << 8 | data[offset], 16)


# Write one byte to HMC5883
def write_byte(adr, value):
    bus.write_byte_data(address, adr, value)


# Main
def main():
    # Check command args
    parser = argparse.ArgumentParser(
        description=(
            "Program to read the gas"
            " counter value by using the digital magnetometer HMC5883."
        )
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        default=False,
        help=(
            "Print out read values and send magnetic induction"
            " via MQTT on piheat/MagInduction"
        ),
    )
    args = parser.parse_args()

    if args.debug:
        print("Write configuration to HMC5883")

    # connect to MQTT broker
    mqttc.connect("pi4server.lan", port=1883, keepalive=60)
    mqttc.loop_start()
    while not mqttc.connected_flag:  # wait in loop
        time.sleep(1)

    # set up rolling counter
    counter = 0

    # Init HMC5883
    write_byte(9, 0b11010001)  # Mode: continuous sampling,
    # output data rate: 10Hz,
    # range: 8G,
    # over sample rate: 64
    write_byte(11, 0b00000001)  # set/reset period as recommended

    # initialize variables
    trigger_state = 0
    tout_wait_time = 0
    last_temperature = None

    logger.info("Starting read from HMC5883")

    while True:
        try:
            # read data from HMC5883
            data = read_data()

            # get x,y,z values of magnetic induction
            bx = convert_sw(data, 0)  # x
            by = convert_sw(data, 2)  # y
            bz = convert_sw(data, 4)  # z

            # get temperature values
            tout = convert_sw(data, 7)

            if args.debug:
                # send scalar magnetic induction via mqtt for visualization
                # b = math.sqrt(float(bx*bx) + float(by*by) + float(bz*bz))
                mqttc.publish("piheat/MagInduction", bz, qos=0)
                print("bz = " + str(bz))

            # check Bz against the trigger level
            old_state = trigger_state
            if bz > TRIGGER_LEVEL + TRIGGER_HYST:
                trigger_state = 1
            elif bz < TRIGGER_LEVEL - TRIGGER_HYST:
                trigger_state = 0
            if old_state == 0 and trigger_state == 1:
                # trigger active -> send update
                logger.debug("Publishing piheat/GasCounterTrigger = CLOSED")
                mqttc.publish("piheat/GasCounterTrigger", "CLOSED", qos=2)
                counter = (counter + 1) % 0xFFFF
                mqttc.publish("piheat/GasCounter", counter, qos=1)
            elif old_state == 1 and trigger_state == 0:
                logger.debug("Publishing piheat/GasCounterTrigger = OPEN")
                mqttc.publish("piheat/GasCounterTrigger", "OPEN", qos=2)

            if last_temperature is None:
                last_temperature = 0.008 * tout + 20.3

            tout_wait_time += 1
            # every 5 minutes report temperature
            if tout_wait_time >= 5 * 60:
                # convert raw value to °C
                temperature = 0.008 * tout + 20.3
                # apply exp. weighted moving average filter
                temperature_ewmf = (1.0 - 0.2) * last_temperature + 0.2 * temperature
                last_temperature = temperature_ewmf
                tout_wait_time = 0
                logger.debug("Publishing piheat/Temperature = " + str(temperature_ewmf))
                infot = mqttc.publish(
                    "piheat/Temperature", "{0:.1f}".format(temperature_ewmf), qos=0
                )

        except IOError as exc:
            logger.error(exc)
        finally:
            time.sleep(1)


if __name__ == "__main__":
    main()
