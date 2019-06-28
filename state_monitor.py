import abc
import json
import operator
from datetime import datetime
from operator import attrgetter
from pathlib import Path
from typing import Any, Callable, MutableMapping, Optional, Tuple, Union

import appdaemon.plugins.hass.hassapi as hass
import attr
from boltons.typeutils import make_sentinel

CheckerReturnType = Tuple[bool, str]
GetIsOkCallableType = Callable[["EntityState", Any, hass.Hass], CheckerReturnType]


@attr.s(auto_attribs=True, cmp=False)
class EntityState:
    """
    Describes how to check the state of an entity.
    """

    entity: str
    is_ok_when: GetIsOkCallableType

    #: By default we check the `state` attribute of the entity, but we can check
    #: anything provided here.  For example, you could set it to `attributes.foobar`.
    entity_attr: str = "state"

    #: The number of seconds the entity has to be in a not ok state before notifying.
    fail_delay: int = 10

    # internal use only
    id: Optional[int] = None

    @property
    def entity_accessor(self) -> str:
        return f"{self.entity}.{self.entity_attr}"

    @property
    def is_setup(self) -> bool:
        return isinstance(self.id, int)


class Checker(abc.ABC):
    """
    Checker instances are used to validate some attribute of the state of an entity.

    Instances of this class are callables that take the arguments as shown on the
    `__call__` method.

    Implementing classes must override the `get_is_ok()` method.  This method is used
    when you call an instance of this class.  As such you can count on having access
    to the instance variables shown in the `__call__` method.  This should be :

    self.es: the `AdvancedEntityState` which contains this checker
    self.actual_val: the actual value in the state
    self.expected_values: The values we're expecting.  of course, this is set in the
        __init__ method, so if you override that, you may have something else.
    self.ha: An instance of the appdaemon HA class.

    Other useful methods that you may or may not want to override:

    get_fail_msg() / get_ok_msg()
    =============================
    Gets the corresponding message to display/send/notify.

    """

    NOT_CALLED = make_sentinel("NOT_CALLED", "NOT_CALLED")

    def __init__(
        self, *expected_values, fail_msg: str = None, ok_msg: str = None
    ) -> None:
        """
        Set up checker with the values you're expecting to find.

        This is just an example implementation, you can of course override init with
        whatever logic and data you want.

        When instantiating the class you can provide functions that

        :param expected_values: A sequence of expected values.
        :param fail_msg: Provide your own "static" failure message.  You can create
            more dynamic messages by overriding `get_fail_msg()`.
        :param ok_msg: Provide your own "static" ok message.  You can create
            more dynamic messages by overriding `get_ok_msg()`.
        """
        self.expected_values = expected_values
        self.static_fail_msg = fail_msg
        self.static_ok_msg = ok_msg

        self.es: Union[EntityState, Checker.NOT_CALLED] = Checker.NOT_CALLED
        self.actual_val: Union[Any, Checker.NOT_CALLED] = Checker.NOT_CALLED
        self.ha: Union[hass.Hass, Checker.NOT_CALLED] = Checker.NOT_CALLED

    @abc.abstractmethod
    def get_is_ok(self) -> bool:
        """
        Whether our data passed the check or not.
        """
        ...

    def _validate_called(self) -> None:
        """Helper method to make sure this instance has been called."""
        assert all(
            (
                self.es != Checker.NOT_CALLED,
                self.actual_val != Checker.NOT_CALLED,
                self.ha != Checker.NOT_CALLED,
            )
        ), "Must call checker instance first."

    def get_fail_msg(self):
        self._validate_called()
        return (
            getattr(self, "static_fail_msg", None)
            or f"{self.es.entity} failed check with a current value of `{self.actual_val}`."
        )

    def get_ok_msg(self):
        self._validate_called()
        return (
            getattr(self, "static_ok_msg", None)
            or f"{self.es.entity} passed check with a current value of `{self.actual_val}`."
        )

    def get_msg(self, is_ok: bool) -> str:
        """
        Get the failure or success message.

        :param is_ok: Whether our data passed the check.
        """
        return self.get_ok_msg() if is_ok else self.get_fail_msg()

    def __call__(self, es: "EntityState", actual_val: str, ha: hass.Hass):
        self.es = es
        self.actual_val = actual_val
        self.ha = ha

        is_ok = self.get_is_ok()

        return is_ok, self.get_msg(is_ok)


class it_is(Checker):
    """Checker that asserts that a value is equal to an expected value."""

    # noinspection PyMissingConstructor
    def __init__(
        self, comparison: str, to: Any, convert_with: Callable[[Any], Any] = None
    ) -> None:
        """
        Creates a callable that compares an expected value to an actual value.


        :param to: The value we're expecting.
        :param comparison: The comparison we're wanting to do.  Must be a string
            representing the name of a function in the `operator` module.  For example,
            `lt` for a less-than comparison
        :param convert_with: An optional callable that takes the actual value and
            converts it to another value before comparison to the expected value.
        """
        assert comparison in dir(
            operator
        ), f"`{comparison}` must be name of a function in the `operator` module."

        if convert_with is None:
            convert_with = lambda x: x

        assert callable(
            convert_with
        ), "Converter must be a callable that takes a value and returns a value."

        self.operation = getattr(operator, comparison)
        self.converter = convert_with
        self.expected_val = to

    def get_is_ok(self) -> bool:
        try:
            converted = self.converter(self.actual_val)
        except ValueError:
            converted = self.actual_val

        try:
            return self.operation(converted, self.expected_val)
        except TypeError:
            return False


class it_is_one_of(Checker):
    """Checker that compares the actual value to a list of acceptable values."""

    def get_is_ok(self) -> bool:
        return self.actual_val in self.expected_values


class it_is_not_one_of(Checker):
    """Checks that the actual value is not one of a list of unacceptable values."""

    def get_is_ok(self) -> bool:
        return self.actual_val not in self.expected_values


it_is_not = it_is_not_one_of

ENTITY_STATES = [
    # Nest cams
    EntityState(
        entity="binary_sensor.front_door_camera_online", is_ok_when=it_is("eq", to="on")
    ),
    EntityState(entity="camera.front_door", is_ok_when=it_is("eq", to="recording")),
    EntityState(
        entity="binary_sensor.garage_camera_online", is_ok_when=it_is("eq", to="on")
    ),
    # Xiaomi flood sensor (water heater)
    EntityState(
        entity="sensor.xiaomi_flood_sensor_1_linkquality",
        is_ok_when=it_is("gt", 30, convert_with=lambda x: int(float(x))),
    ),
    EntityState(
        entity="sensor.xiaomi_flood_sensor_1_voltage",
        is_ok_when=it_is("ge", 3, convert_with=lambda x: int(float(x))),
    ),
    EntityState(
        entity="sensor.xiaomi_flood_sensor_1_battery",
        is_ok_when=it_is("gt", 20, convert_with=lambda x: int(float(x))),
    ),
    # WeMo switch (Morgan's bedside)
    EntityState(
        entity="light.morgans_bedside_lamp", is_ok_when=it_is_one_of("off", "on")
    ),
    # Zooz/Innovelli zwave switch (Morgan's flower lights)
    EntityState(
        entity="switch.zooz_unknown_type2400_id2400_switch",
        is_ok_when=it_is_one_of("off", "on"),
    ),
    EntityState(
        entity="water_heater.heat_pump_water_heater_gen_4",
        is_ok_when=it_is_not("unavailable"),
    ),
    # Xiaomi Click button (Dustin's bedside)
    EntityState(
        entity="sensor.xiaomi_click_1_battery",
        is_ok_when=it_is("gt", 20, convert_with=lambda x: int(float(x))),
    ),
    EntityState(
        entity="sensor.xiaomi_click_1_linkquality",
        is_ok_when=it_is("gt", 30, convert_with=lambda x: int(float(x))),
    ),
    EntityState(
        entity="sensor.xiaomi_click_1_voltage",
        is_ok_when=it_is("ge", 3, convert_with=lambda x: int(float(x))),
    ),
    EntityState(
        entity="cover.garage_door_opener",
        is_ok_when=it_is_one_of("open", "closed", "closing", "opening"),
    ),
    EntityState(entity="light.honeywell_hall", is_ok_when=it_is_one_of("on", "off")),
    # silver lamp entities
    EntityState(entity="light.silver_lamp", is_ok_when=it_is_one_of("on", "off")),
    EntityState(
        entity="light.silver_lamp_bulb_1_light", is_ok_when=it_is_one_of("on", "off")
    ),
    EntityState(
        entity="light.silver_lamp_bulb_2_light", is_ok_when=it_is_one_of("on", "off")
    ),
    EntityState(
        entity="lock.schlage_allegion_be469_touchscreen_deadbolt_locked",
        is_ok_when=it_is_one_of("locked", "unlocked"),
    ),
]

# Set up the ids for all the entity states
for index_, es_ in enumerate(ENTITY_STATES):
    es_.id = index_


class StateMonitor(hass.Hass):
    NOT_FOUND = make_sentinel("NOT_FOUND", "NOT_FOUND")

    # noinspection PyAttributeOutsideInit
    def initialize(self):
        self.current_failures: MutableMapping[int, datetime] = {}
        self.scheduled_re_checks: MutableMapping[int, int] = {}

        for es in ENTITY_STATES:
            assert es.is_setup, "EntityStates have not yet been initialized."

            self.log(f"Doing startup check and registering listener for {es.entity}.")

            # When appdaemon is initializing this app we check all states and alert
            # on them instead of waiting for a state change (which might be a long
            # time or never if the device is already in the failed state).
            self.do_entity_check(es)

            # ... and then we register a state listener
            self.listen_state(self.state_listener, es.entity, es=es)

    def state_listener(self, entity, attribute, old, new, kwargs):
        es: EntityState = kwargs.get("es", None)
        if not es:
            self.log("State listener fired without an attached EntityState", "ERROR")
            return

        self.do_entity_check(es)

    def do_entity_check(self, es: EntityState) -> None:
        """ Checks entity state.

        This is called by our registered state listener.  However, this function does
        not actually do any actions or notifications.  The reason for this is that to
        avoid spurious notifications and actions, this method actually schedule a
        re-check of the entity's state in a few seconds.  This re-check is where
        notifications and actions are fired.
        """
        self.log(f"Checking state of {es.entity_accessor}", "DEBUG")
        is_ok, msg = self.is_ok(es)

        if is_ok and self.is_currently_failed(es):
            # This is something that was not-ok but then came back into compliance.
            self.log(
                f"{es.entity_accessor} failed but came back. Removing from "
                f"current failures.  (msg: {msg})."
            )
            self.do_ok_notify(es, msg)
            # Don't need to track it anymore.
            self.pop_failed(es)
            # In case the entity goes compliant and then not compliant again before
            # the re-check happens we need to make sure there are no scheduled
            # re-checks any time an entity transitions to an OK state.
            self.unschedule_re_check(es)

        elif is_ok:
            # This entity is fine, no need to do anything!
            self.log(f"{es.entity_accessor} is fine.", "DEBUG")

        elif self.is_currently_failed(es):
            # The state of a currently failed entity has changed from one failed
            # state to another failed state, so update action/notification
            self.do_fail_notify(es, msg)

        else:
            # Entity just became non-compliant, so schedule a re-check of its state
            # in a few seconds.  ("few seconds" means however many seconds is in
            # `es.fail_delay`.)
            self.log(
                f"{es.entity_accessor} in fail state. Scheduling recheck in "
                f"{es.fail_delay} seconds.",
                "INFO",
            )
            self.schedule_re_check(es)

    def schedule_re_check(self, es: EntityState):
        self.scheduled_re_checks[es.id] = self.run_in(
            self.re_check, es.fail_delay, es=es
        )

    def unschedule_re_check(self, es: EntityState):
        if es.id in self.scheduled_re_checks:
            self.cancel_timer(self.scheduled_re_checks.pop(es.id))

    def is_ok(self, es: EntityState) -> Tuple[bool, str]:
        """
        Checks if an entity is ok.

        Returns a tuple.  First element is a bool where True means everything is
        fine, and False means OH NOES.  Second element of the tuple is a message
        describing the state.
        """
        value = get_nested_attr(
            self.entities, es.entity_accessor, default=StateMonitor.NOT_FOUND
        )

        # Guard against mis-configuration of EntityStates or when entities have
        # disappeared from HA for some reason.
        if value == StateMonitor.NOT_FOUND:
            err = f"Cannot find `{es.entity_accessor}`"
            self.log(err, "ERROR")
            return False, err

        # The signature for `EntityState.is_ok_when` is the signature of the
        # `Checker.__call__` and `Checker`'s descendents.
        return es.is_ok_when(es, value, self)

    def re_check(self, kwargs) -> None:
        """Do actions/notifications on failed entity state.

        This simple method is the whole point of this class...notifications and
        actions on non-compliant entity states.

        See `do_entity_check` for more info.
        """
        es = kwargs["es"]

        # Don't need to track the handle for the timer that fires this method any
        # longer...because that timer is how this method was run, so our handle to
        # the timer is invalid anyway.
        self.scheduled_re_checks.pop(es.id)
        is_ok, msg = self.is_ok(es)

        if is_ok:
            self.log(f"{es.entity_accessor} was temporarily in a fail state.", "DEBUG")
        else:
            self.add_failed(es)
            self.do_fail_notify(es, msg)

    def do_fail_notify(self, es: EntityState, msg):
        self.log(msg, "WARNING")
        self.call_service(
            "notify/main_html", title="Abnormal State", message=msg, data={"tag": es.id}
        )

    def do_ok_notify(self, es: EntityState, msg):
        self.log(msg, "INFO")
        failed_time = datetime.now() - self.get_failed(es)
        self.call_service(
            "notify/main_html",
            title="Re-Enter Normal State",
            message=f"{msg} (Failed for: {failed_time})",
            data={"tag": es.id},
        )

    def add_failed(self, es: EntityState) -> None:
        if not self.is_currently_failed(es):
            self.current_failures[es.id] = datetime.now()

    def is_currently_failed(self, es: EntityState) -> bool:
        return es.id in self.current_failures

    def get_failed(self, es: EntityState) -> datetime:
        return self.current_failures[es.id]

    def pop_failed(self, es: EntityState) -> datetime:
        return self.current_failures.pop(es.id)


DEFAULT = make_sentinel("DEFAULT", "DEFAULT")


def get_nested_attr(obj, attribute: str, default=DEFAULT):
    """
    Get a named attribute from an object.

    `get_nested_attr(x, 'a.b.c.d')` is equivalent to `x.a.b.c.d`.

    When a default argument is given, it is returned when any attribute in the chain
    doesn't exist; without it, an exception is raised when a missing attribute is
    encountered.
    """
    getter = attrgetter(attribute)
    try:
        return getter(obj)
    except AttributeError:
        if default != DEFAULT:
            return default
        else:
            raise


def write_state_to_file(h: hass.Hass):
    p: Path = Path(__file__).resolve().parent / "state.json"
    with p.open("w") as f:
        json.dump(h.get_state(), f, indent=4, sort_keys=True)
