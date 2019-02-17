"""
Provides the `PercentSchedule` appdaemon app that will call `turn_on` and `turn_off` to keep a 
device on for X percent of the time.

The configuration includes:

    `device`: the hass entity_id of the device we want to toggle on and off
    `percent: the percentage of time, expressed as a float from 0 to 1, that we want the device 
    to be on.
    `min_on_seconds`: the number of seconds the device will be on each time it comes on.

For example, if we have a configuration like this:

```
device: switch.basement_fan
percent: .25
min_on_seconds: 100
```

That configuration will cause `switch.basement_fan` to be turned on for 100 seconds, then it will 
shut off for 300 seconds, then it will turn back on for 100 seconds, and repeat for infinity.

(100 seconds is 25% of 100 + 300 seconds) 
"""
from typing import Tuple

import appdaemon.plugins.hass.hassapi as hass

SECONDS_PER_DAY = 24 * 60 * 60


def get_seconds_off_per_on_second(percent: float) -> float:
    on_seconds = percent * SECONDS_PER_DAY
    off_seconds = SECONDS_PER_DAY - on_seconds
    return off_seconds / on_seconds


def get_on_off_time(percent: float, 
                    min_on_seconds: float = 60) -> Tuple[float, float]:
    return (float(min_on_seconds), 
            get_seconds_off_per_on_second(percent) * min_on_seconds)


class PercentScheduler(hass.Hass):
    def initialize(self):
        self.on_then_off()

    def on_then_off(self, *args, **kwargs):
        if self.get_state(self.args['device']) != 'on':
            self.turn_on(self.args['device'])

        turn_off_in, off_for = get_on_off_time(self.args['percent'], self.args['min_on_seconds'])

        self.log(f'PercentScheduler running {self.args["device"]} for {self.args["percent"] * 100} '
                 f'percent of the time.  It will be on for {turn_off_in} seconds and off '
                 f'for {off_for} seconds.')

        self.log(type(self.args['device']))
        self.log(self.args['device'])
        self.run_in(self.do_turn_off, turn_off_in)
        self.run_in(self.on_then_off, turn_off_in + off_for)

    def do_turn_off(self, *args, **kwargs):
        self.turn_off(self.args['device'])
