#!/usr/bin/python
# *****************************************************************************************************************
#    Pi Power Controller
#    By John M. Wargo
#    www.johnwargo.com
#
# ********************************************************************************************************************

from __future__ import print_function

import sys
import time
from datetime import datetime

import gpiozero
import numpy as np
import pytz
import requests

# from urllib2 import Request, urlopen, URLError

# 'constants' that define the different time triggers used by the application
# DO NOT MODIFY
SETTIME = -1
SUNRISE = -2
SUNSET = -3

# ============================================================================
# User (that's you) adjustable variables
# ============================================================================
# adjust these variables based on your particular hardware configuration

# set this variable to the pin the relay is connected to
RELAY_PIN = 18
# set this variable to the button pin used in your implementation
BUTTON_PIN = 19

# Sunrise and sunset times vary depending on location, so...
# If using sunrise or sunset as trigger options, populate the locLat
# and locLong values with the location's latitude and longitude values
# These values are for Charlotte, NC, to get Sunrise/Sunset values for
# your location, replace these strings with the appropriate values for
# your location.
LOC_LAT = "35.227085"
LOC_LONG = "-80.843124"

# default times for sunrise and sunset. If solar data is enabled, the
# code will reach out every day at 12:01 and populate these values with
# the correct values for the current day. If this fails for any reason,
# the values will fall back to the previous day's values, or, finally,
# these values
time_sunrise = 700
time_sunset = 1900

# Slots array defines time windows and behavior for the relay
# format: [ OnTrigger, OnValue, OffTrigger, OffValue, doRandom]
slots = np.array(
    # ONLY modify the following array with your time settings
    [
        (SETTIME, 700, SETTIME, 900, False),
        (SETTIME, 1700, SETTIME, 2300, True),
        (SUNRISE, 15, SUNSET, -10, True)
    ],
    # leave the rest of this alone
    dtype=[
        ('onTrigger', np.dtype(int)),
        ('onValue', np.dtype(int)),
        ('offTrigger', np.dtype(int)),
        ('offValue', np.dtype(int)),
        ('doRandom', np.dtype(bool))
    ]
)

# Set the local timezone using the data provided here:
tz = pytz.timezone('US/Eastern')
# ============================================================================

# ============================================================================
# Other variables; Please don't modify any of these.
# ============================================================================
# used to help prettify the output
single_hash = "#"
hashes = "#########################################"
slash_n = "\n"

# variable used throughout the application to indicate whether the
# application's current configuration uses the SUNRISE or SUNSET
# triggers
uses_solar_data = False

# API for determining sunrise and sunset times: http://sunrise-sunset.org/api
# Test URL to retrieve data for Charlotte, NC US
# Usage: http://api.sunrise-sunset.org/json?lat=35.2271&lng=-80.8431&date=today
SOLAR_API_URL = "http://api.sunrise-sunset.org/json"
# Make sure you set the local Timezone on the Raspberry Pi for this to
# work correctly

# used to track the current state of the relay. At the start, the
# application turns the relay off then sets this variable's value
# the application can then later query this to determine what the
# current state is for the relay, in the toggle function for example
relay_status = False

# Initialize the btn object and connect it to the button pin
btn = gpiozero.Button(BUTTON_PIN)

# initialize the relay object
relay = gpiozero.OutputDevice(RELAY_PIN, active_high=False, initial_value=False)


def set_relay(status):
    # sets the relay's status based on the boolean value passed to the function
    # a value of True turns the relay on, a value of False turns the relay off
    global relay_status

    relay_status = status
    if status:
        print("Setting relay: ON")
        relay.on()
    else:
        print("Setting relay: OFF")
        relay.off()


def toggle_relay():
    # toggles the relay's status. If the relay is on, when you call this function,
    # it will turn the relay off. If the relay is off, when you call this function,
    # it will turn the relay on. Easy peasy, right?
    global relay_status

    print("toggling relay")
    # flip our relay status value
    relay_status = not relay_status
    # toggle the relay
    relay.toggle()


def init_app():
    global uses_solar_data

    # See if any of our slots require solar data (sunrise, sunset)
    # this drives the slot builder that runs every morning at 12:01 AM
    uses_solar_data = check_for_solar_events()
    if uses_solar_data:
        print("\nSolar data enabled")
        # do we have long and lat values?
        if LOC_LAT or LOC_LONG:
            print("Lat and Long values exist")
        else:
            # then we can't run, and we need to terminate
            print("\nINVALID CONFIGURATION: Lat or Long values missing")
            sys.exit(1)
        get_solar_times()

    # build the daily slots array for today
    build_daily_slots_array()

    # are we supposed to be on?
    if is_on_time():
        # then turn the relay on
        print("\nWhoops, we're supposed to be on!")
        set_relay(True)


def process_loop():
    # initialize the lastMinute variable to the current time minus 1
    # this subtraction isn't technically accurate, but for this purpose,
    # just trying to understand if the minute changed, it's OK
    last_time = get_time_24() - 1
    # infinite loop to continuously check time values
    while 1:
        # Is the button pushed?
        if btn.is_pressed:
            print("Detected button push")
            # Then toggle the relay
            toggle_relay()
        else:
            # otherwise...
            # get the current time (in 24 hour format)
            current_time = get_time_24()
            # is the time the same as the last time we checked?
            if current_time != last_time:
                # No? OK, so the time changed and we may have work to do...
                print(current_time)

                # reset last_time to the current_time (so this doesn't happen until
                # the next minute change
                last_time = current_time

                # build the daily slots array every day at 12:01 AM
                # that's 1 (001) in 24 hour time
                if current_time == 1:
                    # if one of the solar times is enabled
                    if uses_solar_data:
                        # populate our sunrise and sunset values for the day
                        get_solar_times()
                    # build the list of on/off times for today
                    build_daily_slots_array()

                    # finally, check to see if we're supposed to be turning the
                    # relay on or off

        # wait a second then check again
        # You can always increase the sleep value below to check less often
        time.sleep(1)


def validate_slot(slot):
    print("\nValidating slot:", slot)

    # Does the slot use set times and off time is before on time?
    if (slot['onTrigger'] == SETTIME) and (slot['offTrigger'] == SETTIME) and (slot['offTrigger'] < slot['onTrigger']):
        print("Set time: Off time can't be before on time")
        return False

    # Is our solar data delta greater than an hour?
    # this one isn't critical, but when you get into big numbers (multiple hours of time), then the time math
    # gets wonky, so lets just cap it at 60 minutes?
    if ((slot['onTrigger'] < SETTIME) and (slot['onValue'] > 60)) or (
                (slot['offTrigger'] < SETTIME) and (slot['offValue'] > 60)):
        print("Solar Data: Time delta cannot be more than 60 minutes")
        return False

    # we got this far, return True
    print("Slot is valid")
    return True


def validate_slots():
    global slots

    # initialize our error flag
    has_error = False
    # Make sure our slots configuration is valid
    for slot in np.nditer(slots):
        if not validate_slot(slot):
            print("we have an error")
            has_error = True
    return not has_error


def check_for_solar_events():
    # return true if any of the slots use a solar trigger (sunrise or sunset)
    global slots

    # iterate through the slots
    for slot in np.nditer(slots):
        # Do the on or off triggers for this slot use solar?
        if slot['onTrigger'] < SETTIME or slot['offTrigger'] < SETTIME:
            # then we're done, we're solar!
            return True
    return False


def get_solar_times():
    global time_sunrise
    global time_sunset

    format_str = "%I:%M:%S %p"

    print("\nRequesting solar data from", SOLAR_API_URL)
    payload = {"lat": LOC_LAT, "lng": LOC_LONG, "date": "today"}
    try:
        res = requests.get(SOLAR_API_URL, params=payload)
        # did we get a result?
        if res.status_code == requests.codes.ok:
            data = res.json()
            time_val = datetime.strptime(data['results']['sunrise'], format_str)

            time_sunrise = get_time_24_adjusted(time_val)
            print("Sunrise:", data['results']['sunrise'], "(" + str(time_sunrise) + ")")
            time_val = datetime.strptime(data['results']['sunset'], format_str)
            time_sunset = get_time_24_adjusted(time_val)
            print("Sunset:", data['results']['sunset'], "(" + str(time_sunset) + ")")
        else:
            print("Unable to obtain solar data; server returned", res.status_code)
    except ValueError as e:
        print("Value Error:", e)
    except Exception as e:
        print("Unexpected error:", e)


def build_daily_slots_array():
    print("\nBuilding slots array")
    pass


def is_on_time():
    return False


def get_time_24():
    # return the current time in 24 hour format
    the_time = datetime.now()
    # build the 24 hour time using hours and minutes
    if the_time.hour > 0:
        return (the_time.hour * 100) + the_time.minute
    else:
        return the_time.minute


def get_time_24_adjusted(time_val):
    working_time = tz.localize(time_val)
    if working_time.hour > 0:
        return (working_time.hour * 100) + working_time.minute
    else:
        return working_time.minute


# ============================================================================
# here's where we start doing stuff
# ============================================================================
print(slash_n + hashes)
print(single_hash, "Pi Relay Controller                  ", single_hash)
print(single_hash, "By John M. Wargo (www.johnwargo.com) ", single_hash)
print(hashes)

if __name__ == "__main__":
    try:
        # Turn the relay off to start (just to make sure)
        set_relay(False)
        # do we have a valid set of slots?
        if validate_slots():
            init_app()
            process_loop()
        else:
            # then we can't run, and we need to terminate
            print("\nINVALID SLOT(S) DEFINITION")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\nExiting application\n")
        sys.exit(0)
