from dataclasses import asdict, dataclass, field
from numbers import Number
from typing import Any, Dict, List, Optional

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.util.dt import start_of_local_day, utc_from_timestamp
from rctclient.registry import REGISTRY, ObjectInfo

from .api import (
    ApiResponse,
    ApiResponseValue,
    ValidApiResponse,
    get_valid_response_value_or,
)
from .const import (
    BATTERY_MODEL,
    DOMAIN,
    ICON,
    INVERTER_MODEL,
    NAME,
    EntityUpdatePriority,
    MeteredResetFrequency,
)
from .device_class_helpers import guess_device_class_from_unit
from .entry import RctPowerConfigEntryData
from .multi_coordinator_entity import MultiCoordinatorEntity
from .update_coordinator import RctPowerDataUpdateCoordinator


class RctPowerEntity(MultiCoordinatorEntity):
    entity_description: "RctPowerEntityDescription"

    def __init__(
        self,
        coordinators: List[RctPowerDataUpdateCoordinator],
        config_entry: ConfigEntry,
        entity_description: "RctPowerEntityDescription",
    ):
        super().__init__(coordinators)
        self.config_entry = config_entry
        self.entity_description = entity_description

    def get_api_response_by_id(
        self, object_id: int, default: Optional[ApiResponse] = None
    ):
        for coordinator in self.coordinators:
            latest_response = coordinator.get_latest_response(object_id)

            if latest_response is not None:
                return latest_response

        return default

    def get_api_response_by_name(
        self, object_name: str, default: Optional[ApiResponse] = None
    ):
        return self.get_api_response_by_id(
            REGISTRY.get_by_name(object_name).object_id, default
        )

    def get_valid_api_response_value_by_id(
        self, object_id: int, default: Optional[ApiResponseValue] = None
    ):
        return get_valid_response_value_or(
            self.get_api_response_by_id(object_id, None), default
        )

    def get_valid_api_response_value_by_name(
        self, object_name: str, default: Optional[ApiResponseValue] = None
    ):
        return get_valid_response_value_or(
            self.get_api_response_by_name(object_name, None), default
        )

    @property
    def object_infos(self):
        return self.entity_description.object_infos

    @property
    def object_ids(self):
        return [object_info.object_id for object_info in self.object_infos]

    @property
    def config_entry_data(self):
        return RctPowerConfigEntryData.from_config_entry(self.config_entry)

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self.config_entry.entry_id}-{self.object_infos[0].object_id}"

    @property
    def name(self):
        """Return the name of the entity."""
        entity_name = self.entity_description.name or slugify_entity_name(
            self.object_infos[0].name
        )

        return f"{self.config_entry_data.entity_prefix} {entity_name}"

    @property
    def available(self) -> bool:
        return all(
            isinstance(self.get_api_response_by_id(object_id), ValidApiResponse)
            for object_id in self.object_ids
        )

    @property
    def state(self):
        """Return the state of the sensor."""
        value = self.get_valid_api_response_value_by_id(self.object_ids[0], None)

        if isinstance(value, bytes):
            return value.hex()

        if isinstance(value, tuple):
            return None

        if isinstance(value, Number):
            if self.unit_of_measurement == "%":
                value = value * 100

            return round(value,1)

        return value

    @property
    def unit_of_measurement(self):
        if unit_of_measurement := super().unit_of_measurement:
            return unit_of_measurement

        return self.object_infos[0].unit

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        api_responses = (
            self.get_api_response_by_id(object_id) for object_id in self.object_ids
        )

        return {
            "latest_api_responses": [
                asdict(api_response)
                for api_response in api_responses
                if api_response is not None
            ]
        }

    @property
    def device_class(self):
        """Return the device class of the sensor."""
        if device_class := super().device_class:
            return device_class

        if self.unit_of_measurement:
            return guess_device_class_from_unit(self.unit_of_measurement)

        return None


class RctPowerSensorEntity(RctPowerEntity, SensorEntity):
    entity_description: "RctPowerSensorEntityDescription"

    @property
    def last_reset(self):
        """Return time of last reset, if any."""
        if self.entity_description.metered_reset == MeteredResetFrequency.NEVER:
            return None
        elif self.entity_description.metered_reset == MeteredResetFrequency.INITIALLY:
            return utc_from_timestamp(0)
        elif self.entity_description.metered_reset == MeteredResetFrequency.DAILY:
            return start_of_local_day()
        elif self.entity_description.metered_reset == MeteredResetFrequency.MONTHLY:
            return start_of_local_day().replace(day=1)
        elif self.entity_description.metered_reset == MeteredResetFrequency.YEARLY:
            return start_of_local_day().replace(month=1, day=1)


class RctPowerInverterEntity(RctPowerEntity):
    @property
    def device_info(self):
        inverter_sn = str(
            self.get_valid_api_response_value_by_name("inverter_sn", None)
        )

        return DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    "STORAGE",
                    inverter_sn,
                ),
                (
                    DOMAIN,
                    inverter_sn,
                ),
            },  # type: ignore
            name=str(
                self.get_valid_api_response_value_by_name("android_description", ""),
            ),
            sw_version=str(self.get_valid_api_response_value_by_name("svnversion", "")),
            model=INVERTER_MODEL,
            manufacturer=NAME,
        )


class RctPowerInverterSensorEntity(RctPowerInverterEntity, RctPowerSensorEntity):
    pass


class RctPowerInverterFaultEntity(RctPowerEntity):
    @property
    def fault_bitmasks(self):
        return [
            self.get_valid_api_response_value_by_id(object_id, 0)
            for object_id in self.object_ids
        ]

    @property
    def state(self):
        fault_bitmasks = self.fault_bitmasks

        if all(isinstance(bitmask, int) for bitmask in fault_bitmasks):
            return "{0:b}{1:b}{2:b}{3:b}".format(*fault_bitmasks)

        return None

    @property
    def unit_of_measurement(self):
        return None

    @property
    def extra_state_attributes(self):
        return {
            **(super().extra_state_attributes),
            "fault_bitmasks": self.fault_bitmasks,
        }


class RctPowerInverterFaultSensorEntity(
    RctPowerInverterFaultEntity, RctPowerSensorEntity
):
    pass


class RctPowerBatteryEntity(RctPowerEntity):
    @property
    def device_info(self):
        bms_sn = str(self.get_valid_api_response_value_by_name("battery.bms_sn", None))

        return DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    "BATTERY",
                    bms_sn,
                ),
                (
                    DOMAIN,
                    bms_sn,
                ),
            },  # type: ignore
            name=f"Battery at {self.get_valid_api_response_value_by_name('android_description', '')}",
            sw_version=str(
                self.get_valid_api_response_value_by_name(
                    "battery.bms_software_version", ""
                )
            ),
            model=BATTERY_MODEL,
            manufacturer=NAME,
            via_device=(
                DOMAIN,
                str(self.get_valid_api_response_value_by_name("inverter_sn", None)),
            ),
        )


class RctPowerBatterySensorEntity(RctPowerBatteryEntity, RctPowerSensorEntity):
    pass


class RctPowerAttributesEntity(RctPowerEntity):
    @property
    def state(self):
        return f"{len(self.extra_state_attributes.keys())} attributes"

    @property
    def unit_of_measurement(self):
        return None

    @property
    def extra_state_attributes(self):
        return {
            **(super().extra_state_attributes),
            **{
                object_info.name: self.get_valid_api_response_value_by_name(
                    object_info.name, None
                )
                for object_info in self.entity_description.object_infos
            },
        }


@dataclass
class RctPowerEntityDescription(EntityDescription):
    icon: Optional[str] = ICON
    object_infos: List[ObjectInfo] = field(init=False)
    object_names: List[str] = field(default_factory=list)
    update_priority: EntityUpdatePriority = EntityUpdatePriority.FREQUENT

    def __post_init__(self):
        if not self.object_names:
            self.object_names = [self.key]
        self.object_infos = [
            REGISTRY.get_by_name(object_name) for object_name in self.object_names
        ]


@dataclass
class RctPowerSensorEntityDescription(
    RctPowerEntityDescription, SensorEntityDescription
):
    metered_reset: Optional[MeteredResetFrequency] = MeteredResetFrequency.NEVER


def slugify_entity_name(name: str):
    return name.replace(".", "_").replace("[", "_").replace("]", "_").replace("?", "_")


known_faults = [
    "TRAP occurred",
    "RTC can't be configured",
    "RTC 1Hz signal timeout",
    "Hardware Stop by 3.3V fault",
    "Hardware Stop by PWM Logic",
    "Hardware Stop by Uzk overvoltage",
    "Uzk+ is over limit",
    "Uzk- is over limit",
    "Throttle Phase L1 overcurrent",
    "Throttle Phase L2 overcurrent",
    "Throttle Phase L3 overcurrent",
    "Buffer capacitor voltage",
    "Quartz fault",
    "Grid under_voltage phase 1",
    "Grid under_voltage phase 2",
    "Grid under_voltage phase 3",
    "Battery overcurrent",
    "Relays Test failed",
    "Board Over Temperature",
    "Core Over Temperature",
    "Sink 1 Over Temperature",
    "Sink 2 Over Temperature",
    "Error by I2C communication with Power Board",
    "Power Board Error",
    "PWM output ports defect",
    "Insulation is too small or not plausible",
    "I DC Component Max (1 A)",
    "I DC Component Max Slow (47 mA)",
    "One of the DSD channels possibly defect (too big current offset)",
    "Error by RS485 communication with Relays BoxIGBT L1 BH defect",
    "Phase to phase over voltage",
    "IGBT L1 BH defect",
    "IGBT L1 BL defect",
    "IGBT L2 BH defect",
    "IGBT L2 BL defect",
    "IGBT L3 BH defect",
    "IGBT L3 BL defect",
    "Long Term over voltage phase 1",
    "Long Term over voltage phase 2",
    "Long Term over voltage phase 3",
    "Over voltage phase 1, level 1",
    "Over voltage phase 1, level 2",
    "Over voltage phase 2, level 1",
    "Over voltage phase 2, level 2",
    "Over voltage phase 3, level 1",
    "Over voltage phase 3, level 2",
    "Over frequency, level 1",
    "Over frequency, level 2",
    "Under voltage phase 1, level 1",
    "Under voltage phase 1, level 2",
    "Under voltage phase 2, level 1",
    "Under voltage phase 2, level 2",
    "Under voltage phase 3, level 1",
    "Under voltage phase 3, level 2",
    "Under frequency, level 1",
    "Under frequency, level 2",
    "CPU Exception NMI",
    "CPU Exception HardFault",
    "CPU Exception MemManage",
    "CPU Exception BusFault",
    "CPU Exception UsageFault",
    "RTC Power on reset",
    "RTC Oscillation stops",
    "RTC Supply voltage drop",
    "Jump of RCD current DC + AC > 30mA was noticed",
    "Jump of RCD current DC > 60mA was noticed",
    "Jump of RCD current AC > 150mA was noticed",
    "RCD current > 300mA was noticed",
    "incorrect 5V was noticed",
    "incorrect -9V was noticed",
    "incorrect 9V was noticed",
    "incorrect 3V3 was noticed",
    "failure of RDC calibration was noticed",
    "failure of I2C was noticed",
    "afi frequency generator failure",
    "sink temperature too high",
    "Uzk is over limit",
    "Usg A is over limit",
    "Usg B is over limit",
    "Switching On Conditions Umin phase 1",
    "Switching On Conditions Umax phase 1",
    "Switching On Conditions Fmin phase 1",
    "Switching On Conditions Fmax phase 1",
    "Switching On Conditions Umin phase 2",
    "Switching On Conditions Umax phase 2",
    "Battery current sensor defect",
    "Battery booster damaged",
    "Switching On Conditions Umin phase 3",
    "Switching On Conditions Umax phase 3",
    "Voltage surge or average offset is too big on AC-terminals (phase failure detected)",
    "Inverter is disconnected from the household grid",
    "Difference of the measured +9V between DSP and PIC is too big",
    "1.5V error",
    "2.5V error",
    "1.5V measurement difference",
    "2.5V measurement difference",
    "The battery voltage is outside of the expected range",
    "Unable to start the main PIC software",
    "PIC bootloader detected unexpectedly",
    "Phase position error (not 120° as expected)",
    "Battery overvoltage",
    "Throttle current is unstable",
    "Difference between internal and external measured grid voltage is too big in phase",
    "Difference between internal and external measured grid voltage is too big in phase",
    "Difference between internal and external measured grid voltage is too big in phase",
    "External emergency turn off signal is active",
    "Battery is empty, not more energy for standby",
    "CAN communication timeout with battery",
    "Timing problem",
    "Battery IGBT's Heat Sink Over Temperature",
    "Battery heat sink temperature too high",
    "Internal Relays Box error",
    "Relays Box PE off error",
    "Relays Box PE on error",
    "Internal battery error",
    "Parameter changed",
    "3 attempts of island building are failed",
    "Phase to phase under voltage",
    "System reset detected",
    "Update detected",
    "FRT over-voltage",
    "FRT under-voltage",
    "IGBT L1 free wheeling diode defect",
    "IGBT L2 free wheeling diode defect",
    "IGBT L3 free wheeling diode defect",
    "1 phase mode is activated but not allowed for this device class (e.g. 10K)",
    "Island detected",
]
