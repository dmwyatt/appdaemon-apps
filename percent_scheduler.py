"""
Provides the `PercentSchedule` appdaemon app that will call `turn_on` and `turn_off` to keep a
device on for X percent of the time.

The configuration includes:

    `device`: the hass entity_id of the device we want to toggle on and off
    `percent: the percentage of time, expressed as a float from 0 to 1, that we want the device
    to be on.
    `min_on_seconds`: the number of seconds the device will be on each time it comes on.
    `percent_state`: A hass state entity to get percent run time from.  If provided,
    will override `percent`.
    `min_on_seconds_state`: A hass state entity to get number of seconds the device will be on.
    If provided will override `percent`.

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
from typing import Any, ClassVar, Mapping, Optional, Tuple

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
    PERCENT_STATE_KEY: ClassVar[str] = 'percent_state'
    PERCENT_KEY: ClassVar[str] = 'percent'
    MIN_ON_SECONDS_STATE_KEY: ClassVar[str] = 'min_on_seconds_state'
    MIN_ON_SECONDS_KEY: ClassVar[str] = 'min_on_seconds'

    def initialize(self):
        self._timers = []
        self.on_then_off()
        turn_off_in, off_for = get_on_off_time(self.percent, self.min_on_seconds)
        self.log(f'PercentScheduler running {self.device} for {self.percent * 100} '
                 f'percent of the time.  It will be on for {turn_off_in} seconds and off '
                 f'for {off_for} seconds.')
        percent_entity = self.args.get(self.PERCENT_STATE_KEY)
        if percent_entity:
            self.log(f'Tracking percent from {percent_entity}')
            self.listen_state(self.track_state, entity=percent_entity)
        min_on_seconds_entity = self.args.get(self.MIN_ON_SECONDS_STATE_KEY)
        if min_on_seconds_entity:
            self.log(f'Tracking min on seconds from {min_on_seconds_entity}')
            self.listen_state(self.track_state, entity=min_on_seconds_entity)

    def track_state(self, entity: Optional[str],
                    attribute: Optional[str],
                    old: Any, new: Any, kwargs: Mapping[str, Any]):
        percent_entity = self.args.get(self.PERCENT_STATE_KEY)
        min_on_seconds_entity = self.args.get(self.MIN_ON_SECONDS_STATE_KEY)
        self.log(f'{entity} changed from {old} to {new}')
        if entity == percent_entity \
                or attribute == min_on_seconds_entity:
            self.log('hass changed config')
            for handle in self._timers:
                self.log('Canceling existing timer.')
                self.cancel_timer(handle)
            self.log('Rescheduling with new config.')
            self.on_then_off()

    @property
    def percent(self):
        percent_entity = self.args.get(self.PERCENT_STATE_KEY)
        if percent_entity:
            return float(self.get_state(percent_entity)) / 100
        else:
            return float(self.args[self.PERCENT_KEY])

    @property
    def min_on_seconds(self):
        min_on_seconds_entity = self.args.get(self.MIN_ON_SECONDS_STATE_KEY)
        if min_on_seconds_entity:
            return float(self.get_state(min_on_seconds_entity))
        else:
            return float(self.args[self.MIN_ON_SECONDS_KEY])

    @property
    def device(self):
        return self.args['device']

    def get_on_off_time(self):
        percent_entity = self.args.get(self.PERCENT_STATE_KEY)
        turn_off_in, off_for = get_on_off_time(self.percent, self.min_on_seconds)
        return turn_off_in, off_for

    def on_then_off(self, *args, **kwargs):
        turn_off_in, off_for = get_on_off_time(self.percent, self.min_on_seconds)

        if self.get_state(self.device) != 'on':
            self.log(f'Turning {self.device} on.', 'DEBUG')
            self.turn_on(self.device)

        self.log(f'Scheduling turn off of {self.device} '
                 f'in {turn_off_in} seconds.')
        self._timers = [self.run_in(self.do_turn_off, turn_off_in)]

        self.log(f'Scheduling turn on of {self.device} '
                 f'in {turn_off_in + off_for} seconds.')
        self._timers.append(self.run_in(self.on_then_off, turn_off_in + off_for))

    def do_turn_off(self, *args, **kwargs):
        self.turn_off(self.device)
