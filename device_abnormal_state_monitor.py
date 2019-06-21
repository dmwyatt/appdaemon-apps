import abc
import json
import operator
from operator import attrgetter
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Tuple, Union

import appdaemon.plugins.hass.hassapi as hass
import attr
from boltons.typeutils import make_sentinel

NOT_FOUND = make_sentinel("NOT_FOUND", "NOT_FOUND")
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


StatesType = Sequence[str]


def no_ok_states(instance: "EntityState", attribute: str, value: StatesType) -> None:
    if value and instance.ok_states:
        raise ValueError("Cannot provide fault_states if also providing `ok_states`")


def no_fault_states(instance: "EntityState", attribute: str, value: StatesType) -> None:
    if value and instance.fault_states:
        raise ValueError("Cannot provide ok_states if also providing `fault_states`")


def message_optional(
    instance: "EntityState", attribute: str, value: StatesType
) -> None:
    if not instance.is_ok_when and value is None:
        raise ValueError(f"Must provide message if not providing `is_ok_when`. ")


CheckerReturnType = Tuple[bool, str]
GetIsOkCallableType = Callable[["EntityState", Any, hass.Hass], CheckerReturnType]


@attr.s(auto_attribs=True)
class EntityState:
    """
    Describes how to check the state of an entity.
    """

    entity: str
    entity_attr: str = "state"

    #: If provided we use this callable to check the value.  The callable is called
    #: with this EntityState, value we're checking, and the appdaemon api instance
    is_ok_when: Optional[GetIsOkCallableType] = attr.ib(default=None)

    @property
    def entity_accessor(self):
        return f"{self.entity}.{self.entity_attr}"


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
        self, expected_values: Sequence[Any], fail_msg: str = None, ok_msg: str = None
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
        return getattr(self, "static_ok_msg", None) or f"{self.es.entity} passed check."

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


class is_(Checker):
    def __init__(
        self, comparison: str, to: Any, converter: Callable[[Any], Any] = None
    ) -> None:
        """
        Creates a callable that compares an expected value to an actual value.


        :param to: The value we're expecting.
        :param comparison: The comparison we're wanting to do.  Must be a string
            representing the name of a function in the `operator` module.  For example,
            `lt` for a less-than comparison
        :param converter: An optional callable that takes the actual value and
            converts it to another value before comparison to the expected value.
        """
        assert comparison in dir(
            operator
        ), f"`{comparison}` must be name of a function in the `operator` module."

        if converter is None:
            converter = lambda x: x

        assert callable(
            converter
        ), "Converter must be a callable that takes a value and returns a value."

        self.operation = getattr(operator, comparison)
        self.converter = converter
        self.expected_val = to

    def get_is_ok(self) -> bool:
        return self.operation(self.converter(self.actual_val), self.expected_val)


class is_one_of(Checker):
    def get_is_ok(self) -> bool:
        return self.actual_val in self.expected_values


class is_not_one_of(Checker):
    def get_is_ok(self) -> bool:
        return self.actual_val not in self.expected_values


ENTITY_STATES = [
    EntityState(
        entity="binary_sensor.front_door_camera_online", is_ok_when=is_one_of(["on"])
    ),
    EntityState(
        entity="binary_sensor.garage_camera_online", is_ok_when=is_one_of(["on"])
    ),
    EntityState(
        entity="sensor.xiaomi_flood_sensor_1_link_quality",
        is_ok_when=is_not_one_of(["unknown"]),
    ),
    EntityState(entity="camera.front_door", is_ok_when=is_one_of(["recording"])),
    EntityState(
        entity="light.morgans_bedside_lamp", is_ok_when=is_one_of(["off", "on"])
    ),
    EntityState(
        entity="switch.zooz_unknown_type2400_id2400_switch",
        is_ok_when=is_one_of("off", "on"),
    ),
    EntityState(
        entity="water_heater.heat_pump_water_heater_gen_4",
        is_ok_when=is_not_one_of(["unavailable"]),
    ),
    EntityState(
        entity="sensor.xiaomi_click_1_battery",
        is_ok_when=is_("gt", 20, lambda x: int(float(x))),
    ),
    EntityState(
        entity="sensor.xiaomi_click_1_link_quality",
        is_ok_when=is_("gt", 35, lambda x: int(float(x))),
    ),
    EntityState(
        entity="cover.garage_door_opener",
        is_ok_when=is_one_of("open", "closed", "closing"),
    ),
    EntityState(entity="light.honeywell_hall", is_ok_when=is_one_of("on", "off")),
    EntityState(entity="light.silver_lamp", is_ok_when=is_one_of(["on", "off"])),
    EntityState(entity="light.silver_lamp_bulb_1", is_ok_when=is_one_of(["on", "off"])),
    EntityState(entity="light.silver_lamp_bulb_2", is_ok_when=is_one_of(["on", "off"])),
    EntityState(
        entity="lock.schlage_allegion_be469_touchscreen_deadbolt_locked",
        is_ok_when=is_one_of(["locked", "unlocked"]),
    ),
]


def get_es(entity_attr: str):
    found = []
    for es in ENTITY_STATES:
        if entity_attr == es.entity:
            # print(f"{entity_attr} is in {es.entity}")
            found.append(es)

    assert len(found), f"No matching EntityState for {entity_attr}"
    return found


class AbnormalStateMonitor(hass.Hass):
    def initialize(self):
        for es in ENTITY_STATES:
            self.log(f"registering listener for {es.entity}")
            self.listen_state(self.state_listener, es.entity, es=es)

        es = get_es("binary_sensor.front_door_camera_online")[0]
        self.log(self.is_ok(es))

        p: Path = Path(__file__).resolve().parent / "state.json"
        with p.open("w") as f:
            json.dump(self.get_state(), f, indent=4, sort_keys=True)

    def state_listener(self, entity, attribute, old, new, kwargs):
        es = kwargs.get("es", None)
        if not es:
            self.log("State listener fired without an attached EntityState", "WARNING")
        else:
            ok, msg = self.is_ok(es)

            if not ok:
                self.notify(msg, name="main_html")
                self.log(msg, "WARNING")

    def is_ok(self, es: EntityState) -> Tuple[bool, str]:
        """
        Checks an entity's state.

        Returns a tuple.  First element is a bool where True means everything is
        fine, and False means OH NOES.  Second element of the tuple is a message
        describing the state.
        """
        value = get_nested_attr(self.entities, es.entity_accessor, default=NOT_FOUND)

        if value == NOT_FOUND:
            err = f"Cannot find `{es.entity_attr}`"
            self.log(err, "ERROR")
            return False, err

        # bypass our later simplistic checks and use the checker callable
        if hasattr(es, "is_ok_when") and callable(es.is_ok_when):
            return es.is_ok_when(es, value, self)

        def get_msg(passed: bool) -> str:
            ok_msg = es.ok_msg or "passed check"
            fail_msg = es.fail_msg or "failed check"
            return (
                f"{es.entity_accessor}: {ok_msg}"
                if passed
                else f"{es.entity_accessor}: {fail_msg}"
            )

        if es.fault_states:
            passed = value not in es.fault_states
        else:
            passed = value in es.ok_states

        return passed, get_msg(passed)
