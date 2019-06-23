import abc
import operator
from operator import attrgetter
from typing import Any, Callable, Sequence, Tuple, Union
from uuid import uuid4

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


CheckerReturnType = Tuple[bool, str]
GetIsOkCallableType = Callable[["EntityState", Any, hass.Hass], CheckerReturnType]


def id_factory():
    return str(uuid4())


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

    id: str = attr.ib(factory=id_factory)

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
        return self.operation(self.converter(self.actual_val), self.expected_val)


class it_is_one_of(Checker):
    """Checker that compares the actual value to a list of acceptable values."""

    def get_is_ok(self) -> bool:
        return self.actual_val in self.expected_values


class is_not_one_of(Checker):
    """Checks that the actual value is not one of a list of unacceptable values."""

    def get_is_ok(self) -> bool:
        return self.actual_val not in self.expected_values


ENTITY_STATES = [
    EntityState(
        entity="binary_sensor.front_door_camera_online", is_ok_when=it_is("eq", to="on")
    ),
    EntityState(
        entity="binary_sensor.garage_camera_online", is_ok_when=it_is("eq", to="on")
    ),
    EntityState(
        entity="sensor.xiaomi_flood_sensor_1_link_quality",
        is_ok_when=is_not_one_of("unknown"),
    ),
    EntityState(entity="camera.front_door", is_ok_when=it_is("eq", to="recording")),
    EntityState(
        entity="light.morgans_bedside_lamp", is_ok_when=it_is_one_of("off", "on")
    ),
    EntityState(
        entity="switch.zooz_unknown_type2400_id2400_switch",
        is_ok_when=it_is_one_of("off", "on"),
    ),
    EntityState(
        entity="water_heater.heat_pump_water_heater_gen_4",
        is_ok_when=is_not_one_of("unavailable"),
    ),
    EntityState(
        entity="sensor.xiaomi_click_1_battery",
        is_ok_when=it_is("gt", 20, convert_with=lambda x: int(float(x))),
    ),
    EntityState(
        entity="sensor.xiaomi_click_1_link_quality",
        is_ok_when=it_is("gt", 35, convert_with=lambda x: int(float(x))),
    ),
    EntityState(
        entity="cover.garage_door_opener",
        is_ok_when=it_is_one_of("open", "closed", "closing"),
    ),
    EntityState(entity="light.honeywell_hall", is_ok_when=it_is_one_of("on", "off")),
    EntityState(entity="light.silver_lamp", is_ok_when=it_is_one_of("on", "off")),
    EntityState(
        entity="light.silver_lamp_bulb_1", is_ok_when=it_is_one_of("on", "off")
    ),
    EntityState(
        entity="light.silver_lamp_bulb_2", is_ok_when=it_is_one_of("on", "off")
    ),
    EntityState(
        entity="lock.schlage_allegion_be469_touchscreen_deadbolt_locked",
        is_ok_when=it_is_one_of("locked", "unlocked"),
    ),
]


class AbnormalStateMonitor(hass.Hass):
    # noinspection PyAttributeOutsideInit
    def initialize(self):
        for es in ENTITY_STATES:
            self.log(f"registering listener for {es.entity}")
            self.listen_state(self.state_listener, es.entity, es=es)

            self.current_failures = []

        # p: Path = Path(__file__).resolve().parent / "state.json"
        # with p.open("w") as f:
        #     json.dump(self.get_state(), f, indent=4, sort_keys=True)

    def state_listener(self, entity, attribute, old, new, kwargs):
        es: EntityState = kwargs.get("es", None)
        if not es:
            self.log("State listener fired without an attached EntityState", "ERROR")
            return

        self.log(f"Checking state of {es.entity_accessor}", "DEBUG")
        ok, msg = self.is_ok(es)

        if ok:
            # Check if this is something that failed and then returned to an ok state.
            if es in self.current_failures:
                self.do_ok_notify(es, msg)
                self.current_failures.pop(self.current_failures.index(es))

        else:
            self.log(
                f"{es.entity_accessor} in fail state. Scheduling recheck in "
                f"{es.fail_delay} seconds.",
                "INFO",
            )
            self.run_in(self.initial_failure_callback, es.fail_delay, es=es)

    def is_ok(self, es: EntityState) -> Tuple[bool, str]:
        """
        Checks an entity's state.

        Returns a tuple.  First element is a bool where True means everything is
        fine, and False means OH NOES.  Second element of the tuple is a message
        describing the state.
        """
        value = get_nested_attr(self.entities, es.entity_accessor, default=NOT_FOUND)

        # Guard against mis-configuration of EntityStates or when entities have
        # disappeared from HA for some reason.
        if value == NOT_FOUND:
            err = f"Cannot find `{es.entity_attr}`"
            self.log(err, "ERROR")
            return False, err

        return es.is_ok_when(es, value, self)

    def initial_failure_callback(self, kwargs) -> None:
        es = kwargs["es"]
        is_ok, msg = self.is_ok(es)

        if is_ok:
            self.log(f"{es.entity_accessor} was temporarily in a fail state.", "DEBUG")
        else:
            self.fail_es(es)
            self.do_fail_notify(es, msg)
            # self.notify(msg, name="main_html")

    def fail_es(self, es: EntityState) -> str:
        if es in self.current_failures:
            return es.id

        else:
            self.current_failures.append(es)
            return es.id

    def do_fail_notify(self, es: EntityState, msg):
        self.log(msg, "WARNING")
        self.call_service(
            "notify/main_html", title="Abnormal State", message=msg, data={"tag": es.id}
        )

    def do_ok_notify(self, es: EntityState, msg):
        self.log(msg, "INFO")
        self.call_service(
            "notify/main_html",
            title="Re-Enter Normal State",
            message=msg,
            data={"tag": es.id},
        )
