"""DataUpdateCoordinator for our integration."""

from datetime import datetime, timedelta
from homeassistant.util import slugify, dt as dt_util
import logging
import asyncio

from typing import Any

from .runtime.storage import IrrigationStorage, PersistentIrrigationState
from .entities.entity_data import build_all_entity_data
from .runtime.switch_tracking import StationSwitchTracker
from .runtime.scheduler import IrrigationScheduler
from .runtime.schedule_state import ScheduleStateManager
from .runtime.weather_runtime import WeatherRuntime
from .runtime.irrigation_runner import IrrigationRunner
from .runtime.active_irrigation import (
    ActiveIrrigationState,
    empty_controller_attributes,
    empty_station_attributes,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_SCAN_INTERVAL,
    CONF_NAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .helpers.util import ensure_datetime, ensure_aware, get_controller_service_prefix
from .models.irrigation import IrrigationController, IrrigationStation
from .api.controller import SmartWaterControllerAPI
from .const import (
    DEFAULT_SCAN_INTERVAL,
    CONTROLLER_MAC_ADDRESS,
    SERVICE_ACTIONS,
    WEATHER_API_CACHE_TIMEOUT,
    WEATHER_API_CACHE_DEFAULT_TIMEOUT,
    DOMAIN,
    IRRIGATION_CONTROL_METHOD,
    IRRIGATION_CONTROL_METHOD_SERVICE,
    IRRIGATION_CONTROL_METHOD_SWITCH,
    STATION_SWITCH_ENTITIES,
    SPRINKLE_WITH_RAIN,
    BLUETOOTH_TIMEOUT,
    BLUETOOTH_DEFAULT_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


def _extract_mac_address(value: str) -> str:
    """Extract a MAC address from either 'Name - AA:BB:CC...' or a raw MAC string."""
    if not value:
        return ""
    text = str(value).strip()
    if " - " in text:
        return text.rsplit(" - ", 1)[1].strip()
    return text


class SmartWaterControllerCoordinator(DataUpdateCoordinator):
    """Smart Water Controller coordinator."""

    data: list[dict[str, Any]]

    @property
    def controller_mac_address(self) -> str:
        """Return configured controller MAC, if any."""
        return (getattr(self, "_controller_mac_address", "") or "").strip()

    @property
    def controller_display_name(self) -> str:
        """Return the name shown in the device registry."""
        mac = self.controller_mac_address
        if mac:
            return mac

        # Fallback: use CONF_NAME/title and prefix with domain, slugified
        raw_name = (
            (self.config_entry.data.get(CONF_NAME) or "").strip()
            or (self.config_entry.title or "").strip()
            or "controller"
        )
        return f"{DOMAIN}_{slugify(raw_name)}"

    @property
    def controller_unique_prefix(self) -> str:
        """Return a stable prefix for entity unique_ids."""
        mac = self.controller_mac_address
        if mac:
            return mac

        # Stable fallback: entry_id never changes
        return self.config_entry.entry_id
    
    @property
    def controller_service_prefix(self) -> str:
        """Prefix used to name services etc."""

        raw_name = (
            (self.config_entry.data.get(CONF_NAME) or "").strip()
            or (self.config_entry.title or "").strip()
            or "controller"
        )
        return get_controller_service_prefix(
            controller_mac=self.controller_mac_address,
            controller_name=raw_name,
        )

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize coordinator."""
        self.hass = hass
        self.config_entry = config_entry
    
        controller_mac_source = (
            (config_entry.unique_id or "").strip()
            or (config_entry.data.get(CONTROLLER_MAC_ADDRESS, "") or "").strip()
        )
        self._controller_mac_address = _extract_mac_address(controller_mac_source)
    
        _LOGGER.info(f"{self.controller_mac_address} - Starting Coordinator")
    
        self.poll_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            DEFAULT_SCAN_INTERVAL,
        )
        self.bluetooth_timeout = self.config_entry.options.get(
            BLUETOOTH_TIMEOUT,
            BLUETOOTH_DEFAULT_TIMEOUT,
        )
        self.weather_api_timeout = self.config_entry.options.get(
            WEATHER_API_CACHE_TIMEOUT,
            WEATHER_API_CACHE_DEFAULT_TIMEOUT,
        )
    
        self.sprinkle_with_rain = (
            self.config_entry.data.get(SPRINKLE_WITH_RAIN, "false") == "true"
        )
    
        self.soil_moisture_sensor = self.config_entry.data.get("soil_moisture_sensor")
        self.soil_moisture_threshold = float(
            self.config_entry.data.get("soil_moisture_threshold", 0)
        )
    
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({config_entry.unique_id})",
            update_method=self.async_update_data,
            update_interval=timedelta(seconds=self.poll_interval),
        )
    
        self.num_stations = self.config_entry.data.get("num_stations", 2)
        self.station_areas = self.config_entry.data.get(
            "station_areas",
            [0] * self.num_stations,
        )
        if not isinstance(self.station_areas, list) or len(self.station_areas) != self.num_stations:
            _LOGGER.warning(
                f"{self.controller_mac_address} - station_areas missing or invalid, setting defaults."
            )
            self.station_areas = [0] * self.num_stations
    
        self.station_switch_entities = self.config_entry.data.get(STATION_SWITCH_ENTITIES, [])
        if not isinstance(self.station_switch_entities, list):
            self.station_switch_entities = []
    
        self.station_switch_tracker = StationSwitchTracker(self)
        self.irrigation_scheduler = IrrigationScheduler(self)
        self.schedule_state = ScheduleStateManager(self)
        self.weather_runtime = WeatherRuntime(self)
        self.irrigation_runner = IrrigationRunner(self)
    
        self.controller = IrrigationController(
            device_id=f"{self.controller_mac_address}_irrigation_controller_status",
            device_name="Controller Status",
            device_uid="",
            software_version="1.0",
            icon="mdi:state-machine",
        )
    
        station_names = self.config_entry.data.get("station_names", [])
        if not isinstance(station_names, list) or len(station_names) != self.num_stations:
            station_names = [f"Station {i}" for i in range(1, self.num_stations + 1)]
        self.station_names = station_names
    
        self.stations = [
            IrrigationStation(
                device_id=f"{self.controller_mac_address}_irrigation_station_{station_id}_status",
                device_name=f"{station_names[station_id - 1]} Status",
                device_uid="",
                station_number=station_id,
                software_version="1.0",
                icon="mdi:state-machine",
            )
            for station_id in range(1, self.num_stations + 1)
        ]
    
        self.api = SmartWaterControllerAPI(
            self.hass,
            controller_mac=self.controller_mac_address,
            bluetooth_timeout=self.bluetooth_timeout,
            service_actions=config_entry.data.get(SERVICE_ACTIONS, {}),
            station_switch_entities=config_entry.data.get(STATION_SWITCH_ENTITIES, []),
        )
    
        self.irrigation_control_method = self.config_entry.data.get(
            IRRIGATION_CONTROL_METHOD,
            IRRIGATION_CONTROL_METHOD_SERVICE,
        )
    
        self.active_irrigation: ActiveIrrigationState | None = None
        self.current_irrigation: ActiveIrrigationState | None = None
    
        self.storage = IrrigationStorage(
            hass,
            f"irrigation_{config_entry.unique_id}",
            num_stations=self.num_stations,
            station_name_resolver=self._get_station_name,
            log_prefix=f"{self.controller_mac_address} - ",
        )
        self.irrigation_stop_event = asyncio.Event()
    
        self.schedule: list[dict[str, Any]] | None = None
        self.next_schedule: datetime | None = None
    
        self.last_reset = dt_util.now()
        self.last_rain = dt_util.now()
        self.last_sprinkle = dt_util.now()
    
        self.will_it_rain_today = False
        self.will_it_rain_today_forecast = []
        self.has_rained_today = False
        self.is_raining_now = False
        self.is_raining_now_json = {}
    
        self.rain_time_today = 0
        self.rain_total_amount_today = 0
        self.rain_total_amount_forecasted_today = 0
        self.total_water_consumption = 0
    
        self.irrigation_manual_duration = 10
        self.water_flow_rate = [12] * self.num_stations
        self.sprinkle_total_amount_today = [0.0] * self.num_stations
        self.sprinkle_target_amount_today = [0.0] * self.num_stations
        self.forecasted_sprinkle_today = [0.0] * self.num_stations
        self.remaining_sprinkle_today = [0.0] * self.num_stations
    
        self.weather_runtime.configure()
    
        self.init_task = hass.async_create_task(self.async_init())
    
        _LOGGER.info(f"{self.controller_mac_address} - Coordinator initialization finished!")


    async def update_config(self, new_config: ConfigEntry):
        """Update the coordinator with new configuration."""
        _LOGGER.info(f"{self.controller_mac_address} - Updating Coordinator with new config...")
        self.config_entry = new_config
    
        self._controller_mac_address = _extract_mac_address(
            self.config_entry.data.get(CONTROLLER_MAC_ADDRESS, "")
        )
    
        self.sprinkle_with_rain = (
            self.config_entry.data.get(SPRINKLE_WITH_RAIN, "false") == "true"
        )
    
        self.soil_moisture_sensor = self.config_entry.data.get("soil_moisture_sensor")
    
        try:
            self.soil_moisture_threshold = float(
                self.config_entry.data.get("soil_moisture_threshold", 0)
            )
        except (TypeError, ValueError):
            self.soil_moisture_threshold = 0.0
            _LOGGER.warning(
                f"{self.controller_mac_address} - Invalid soil_moisture_threshold; using 0.0"
            )
    
        self.poll_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            DEFAULT_SCAN_INTERVAL,
        )
        self.update_interval = timedelta(seconds=self.poll_interval)
        self.bluetooth_timeout = self.config_entry.options.get(
            BLUETOOTH_TIMEOUT,
            BLUETOOTH_DEFAULT_TIMEOUT,
        )
        self.weather_api_timeout = self.config_entry.options.get(
            WEATHER_API_CACHE_TIMEOUT,
            WEATHER_API_CACHE_DEFAULT_TIMEOUT,
        )
    
        self.weather_runtime.configure()
    
        self.num_stations = self.config_entry.data.get("num_stations", 2)
        self.storage.update_num_stations(self.num_stations)
    
        self.station_areas = self.config_entry.data.get(
            "station_areas",
            [0] * self.num_stations,
        )
        if not isinstance(self.station_areas, list) or len(self.station_areas) != self.num_stations:
            _LOGGER.warning(
                f"{self.controller_mac_address} - station_areas missing or invalid on update, setting defaults."
            )
            self.station_areas = [0] * self.num_stations
    
        self.station_switch_entities = self.config_entry.data.get(STATION_SWITCH_ENTITIES, [])
        if not isinstance(self.station_switch_entities, list):
            self.station_switch_entities = []
    
        self.api = SmartWaterControllerAPI(
            self.hass,
            controller_mac=self.controller_mac_address,
            bluetooth_timeout=self.bluetooth_timeout,
            service_actions=self.config_entry.data.get(SERVICE_ACTIONS, {}),
            station_switch_entities=self.station_switch_entities,
        )
    
        station_names = self.config_entry.data.get("station_names", [])
        if not isinstance(station_names, list) or len(station_names) != self.num_stations:
            station_names = [f"Station {i}" for i in range(1, self.num_stations + 1)]
        self.station_names = station_names
    
        self.stations = [
            IrrigationStation(
                device_id=f"{self.controller_mac_address}_irrigation_station_{station_id}_status",
                device_name=f"{station_names[station_id - 1]} Status",
                device_uid="",
                station_number=station_id,
                software_version="1.0",
                icon="mdi:state-machine",
            )
            for station_id in range(1, self.num_stations + 1)
        ]
    
        await self.initialize_schedule()
        await self.station_switch_tracker.setup()
        await self.async_request_refresh()
    
        _LOGGER.info(f"{self.controller_mac_address} - Updated Coordinator with new config.")


    async def load_persistent_data(self):
        """Load persistent data from storage."""
        state = await self.storage.async_load()
        self._apply_persistent_state(state)
        self.weather_runtime.restore_cache()
    
        _LOGGER.info(f"{self.controller_mac_address} - Persistent data loaded.")

    def _apply_persistent_state(self, state: PersistentIrrigationState) -> None:
        """Apply persistent state to the coordinator."""
        self.will_it_rain_today = state.will_it_rain_today
        self.will_it_rain_today_forecast = state.will_it_rain_today_forecast
        self.has_rained_today = state.has_rained_today
        self.is_raining_now = state.is_raining_now
        self.is_raining_now_json = state.is_raining_now_json
    
        self.last_reset = state.last_reset
        self.last_sprinkle = state.last_sprinkle
        self.last_rain = state.last_rain
    
        self.irrigation_manual_duration = state.irrigation_manual_duration
        self.water_flow_rate = state.water_flow_rate
    
        self.rain_time_today = state.rain_time_today
        self.rain_total_amount_today = state.rain_total_amount_today
        self.rain_total_amount_forecasted_today = state.rain_total_amount_forecasted_today
        self.total_water_consumption = state.total_water_consumption
    
        self.sprinkle_total_amount_today = state.sprinkle_total_amount_today
        self.sprinkle_target_amount_today = state.sprinkle_target_amount_today
        self.forecasted_sprinkle_today = state.forecasted_sprinkle_today
        self.remaining_sprinkle_today = state.remaining_sprinkle_today
    
        self.schedule = state.schedule
        self.active_irrigation = state.active_irrigation
        
    async def save_persistent_data(self):
        """Save persistent data to storage."""
        await self.storage.async_save(self._build_persistent_state())

    def _build_persistent_state(self) -> PersistentIrrigationState:
        """Build persistent state from the coordinator."""
        return PersistentIrrigationState(
            will_it_rain_today=self.will_it_rain_today,
            will_it_rain_today_forecast=self.will_it_rain_today_forecast,
            has_rained_today=self.has_rained_today,
            is_raining_now=self.is_raining_now,
            is_raining_now_json=self.is_raining_now_json,
            last_reset=ensure_aware(self.last_reset),
            last_sprinkle=ensure_aware(self.last_sprinkle or datetime.min),
            last_rain=ensure_aware(self.last_rain or datetime.min),
            irrigation_manual_duration=self.irrigation_manual_duration,
            water_flow_rate=self.water_flow_rate,
            rain_time_today=self.rain_time_today,
            rain_total_amount_today=self.rain_total_amount_today,
            rain_total_amount_forecasted_today=self.rain_total_amount_forecasted_today,
            total_water_consumption=self.total_water_consumption,
            sprinkle_total_amount_today=self.sprinkle_total_amount_today,
            sprinkle_target_amount_today=self.sprinkle_target_amount_today,
            forecasted_sprinkle_today=self.forecasted_sprinkle_today,
            remaining_sprinkle_today=self.remaining_sprinkle_today,
            schedule=self.schedule,
            active_irrigation=self.active_irrigation,
        )


    async def async_init(self):
        """Initialize APIs, persistent data and scheduled tasks."""
        await self.load_persistent_data()
    
        # If we were watering using switch-based control when HA restarted,
        # ensure the station is not left running indefinitely.
        await self.irrigation_runner.restore_active_irrigation()
    
        _LOGGER.info(f"{self.controller_mac_address} - Connecting to SmartWaterController API...")
    
        try:
            await self.api.connect()
            _LOGGER.info(f"{self.controller_mac_address} - Connected to SmartWaterController API")
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.warning(
                f"{self.controller_mac_address} - Failed connecting to SmartWaterController device "
                f"({self.controller_mac_address})!, ex={ex}"
            )
    
        await self.initialize_schedule()
        await self.station_switch_tracker.setup()
    
        # Run immediately after initialization.
        await self.check_and_schedule_watering()
        await self.irrigation_scheduler.setup_daily_tasks()
        self.data = await self.async_update_all_sensors()

    def _is_switch_control_method(self) -> bool:
        """Return True if this config entry uses switch-based control."""
        return self.irrigation_control_method == IRRIGATION_CONTROL_METHOD_SWITCH
    

    def _get_station_name(self, station_id: int | None) -> str | None:
        """Return the configured station name."""
        if station_id is None:
            return None
    
        index = station_id - 1
    
        if (
            isinstance(getattr(self, "station_names", None), list)
            and 0 <= index < len(self.station_names)
        ):
            return self.station_names[index]
    
        return f"Station {station_id}"


    def _get_current_irrigation_station_id(self) -> int | None:
        """Return the current irrigation station id."""
        if self.current_irrigation is None:
            return None
    
        return int(self.current_irrigation.station)
    
    
    def get_current_irrigation_station_name(self) -> str:
        """Return the current irrigation station name."""
        if self.current_irrigation is None:
            return "Idle"
    
        return self.current_irrigation.station_name or self._get_station_name(
            self.current_irrigation.station
        ) or f"Station {self.current_irrigation.station}"
    
    
    def get_current_irrigation_end_time(self) -> datetime | None:
        """Return the current irrigation end time."""
        if self.current_irrigation is None:
            return None
    
        return self.current_irrigation.end_at
    
    
    def get_current_irrigation_started_at(self) -> datetime | None:
        """Return the current irrigation start time."""
        if self.current_irrigation is None:
            return None
    
        return self.current_irrigation.start_at


    def _set_current_irrigation(
        self,
        *,
        station: int,
        source: str,
        duration_minutes: int | None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> None:
        """Set current irrigation metadata."""
        self.current_irrigation = ActiveIrrigationState.create(
            station=station,
            source=source,
            duration_minutes=duration_minutes,
            station_name=self._get_station_name(station),
            start_at=start_at,
            end_at=end_at,
        )
    
    
    def _clear_current_irrigation(self, station: int | None = None) -> None:
        """Clear current irrigation metadata."""
        if station is not None:
            current_station = self._get_current_irrigation_station_id()
    
            if current_station is not None and current_station != int(station):
                return
    
        self.current_irrigation = None
    
    
    def get_controller_irrigation_attributes(self) -> dict[str, Any]:
        """Return current irrigation attributes for the controller status sensor."""
        if self.current_irrigation is None:
            return empty_controller_attributes()
    
        return self.current_irrigation.controller_attributes()
    
    
    def get_station_irrigation_attributes(self, station_id: int) -> dict[str, Any]:
        """Return current irrigation attributes for a station status sensor."""
        if self.current_irrigation is None:
            return empty_station_attributes()
    
        return self.current_irrigation.station_attributes(station_id)
        

    def _get_valid_watering_hours(self, month_config: dict[str, Any]) -> list[str]:
        """Return valid watering hours sorted by time."""
        return self.schedule_state.get_valid_watering_hours(month_config)
    
    
    def _get_future_watering_slots_for_day(
        self,
        day,
        month_config: dict[str, Any],
    ) -> list[tuple[str, datetime]]:
        """Return future watering slots for the provided day."""
        return self.schedule_state.get_future_watering_slots_for_day(day, month_config)
    
    
    def _latest_rain_or_sprinkle_event(self) -> datetime | None:
        """Return the latest rain or sprinkle event."""
        return self.schedule_state.latest_rain_or_sprinkle_event()
    
    
    def _recent_event_blocks_today(self, interval_days: int) -> bool:
        """Check if a previous event should block watering today."""
        return self.schedule_state.recent_event_blocks_today(interval_days)
    
    
    def _rain_blocks_watering_now(self) -> bool:
        """Check if watering should be blocked right now because it is currently raining."""
        if self.sprinkle_with_rain:
            return False
    
        return bool(self.is_raining_now)

    async def calculate_sprinkle_target_amounts(
        self,
        *,
        only_future_slots: bool = False,
        include_already_applied: bool = False,
    ) -> list[float]:
        """Calculate today's target sprinkle amount per station."""
        return await self.schedule_state.calculate_sprinkle_target_amounts(
            only_future_slots=only_future_slots,
            include_already_applied=include_already_applied,
        )


    async def reset_rain_sprinkle_indicators(self, *_):
        """Reset rain and sprinkle indicators."""
        self.has_rained_today = False
        self.will_it_rain_today = False
        self.rain_time_today = 0
        self.rain_total_amount_today = 0
        self.sprinkle_total_amount_today = [0.0] * self.num_stations
    
        if self.weather_api:
            self.rain_total_amount_forecasted_today = await self.weather_api.get_total_rain_forecast_for_today()
        else:
            self.rain_total_amount_forecasted_today = 0
    
        self.sprinkle_target_amount_today = await self.calculate_sprinkle_target_amounts()
    
        self.forecasted_sprinkle_today = [
            self.calculate_forecasted_sprinkle_today(station_id)
            for station_id in range(1, self.num_stations + 1)
        ]
    
        self.remaining_sprinkle_today = [
            self.calculate_remaining_sprinkle_today(station_id)
            for station_id in range(1, self.num_stations + 1)
        ]
    
        self.last_reset = dt_util.now()
    
        _LOGGER.info(f"{self.controller_mac_address} - Reset rain and sprinkle indicators.")
    
        await self.save_persistent_data()

    def needs_watering_today(self) -> bool:
        """Check if any station still needs watering today."""
        return self.schedule_state.needs_watering_today()


    async def check_and_schedule_watering(self, *_):
        """Check if watering can run today and schedule future watering slots."""
        await self.irrigation_scheduler.check_and_schedule_watering()
        

    async def get_next_watering_date(self) -> datetime | None:
        """Get the next watering time considering today's remaining slots."""
        return await self.schedule_state.get_next_watering_date()

    async def run_watering_cycle(self, *_, scheduled_hour: str | None = None):
        """Run the scheduled watering cycle if all conditions are met."""
        await self.irrigation_runner.run_watering_cycle(scheduled_hour=scheduled_hour)

    async def async_update_all_sensors(self):
        """Update all coordinator entity data."""
        _LOGGER.debug(f"{self.controller_mac_address} - Updating all sensors...")
    
        if not hasattr(self, "rain_time_today") or self.rain_time_today is None:
            self.rain_time_today = 0
    
        if not hasattr(self, "rain_total_amount_today") or self.rain_total_amount_today is None:
            self.rain_total_amount_today = 0
    
        if (
            not hasattr(self, "rain_total_amount_forecasted_today")
            or self.rain_total_amount_forecasted_today is None
        ):
            self.rain_total_amount_forecasted_today = 0
    
        if not hasattr(self, "last_reset"):
            self.last_reset = None
    
        now = dt_util.now()
    
        if now.time() > datetime.strptime("00:05:00", "%H:%M:%S").time():
            try:
                self.last_reset = ensure_datetime(self.last_reset)
    
                if self.last_reset.date() != now.date():
                    _LOGGER.info(
                        f"{self.controller_mac_address} - Last reset was on {self.last_reset.date()}, "
                        "performing daily reset."
                    )
                    await self.reset_rain_sprinkle_indicators()
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.warning(
                    f"{self.controller_mac_address} - Could not determine last_reset date. "
                    f"Skipping reset. Error: {err}"
                )
    
        await self.weather_runtime.refresh()
    
        self.forecasted_sprinkle_today = [
            self.calculate_forecasted_sprinkle_today(station_id)
            for station_id in range(1, self.num_stations + 1)
        ]
    
        self.remaining_sprinkle_today = [
            self.calculate_remaining_sprinkle_today(station_id)
            for station_id in range(1, self.num_stations + 1)
        ]
    
        self.next_schedule = await self.get_next_watering_date()
    
        data = build_all_entity_data(self)
    
        await self.save_persistent_data()
    
        _LOGGER.debug(f"{self.controller_mac_address} - Updated sensors.")
        return data



    async def async_update_data(self):
        data = []

        try:
            data = await self.async_update_all_sensors()
        except Exception as err:
            _LOGGER.error(f"{self.controller_mac_address} - Error: {err}", exc_info=True)

        # What is returned here is stored in self.data by the DataUpdateCoordinator
        return data


    def calculate_forecasted_sprinkle_today(self, station_id: int) -> float:
        """Calculate the planned sprinkle amount for today for a station."""
        return self.schedule_state.calculate_forecasted_sprinkle_today(station_id)
    
    
    def calculate_remaining_sprinkle_today(self, station_id: int) -> float:
        """Calculate the remaining sprinkle amount needed today for a station."""
        return self.schedule_state.calculate_remaining_sprinkle_today(station_id)


    async def start_irrigation(
        self,
        station: int,
        minutes: int | None = None,
        *,
        source: str = "manual",
    ):
        """Start irrigation on a station."""
        await self.irrigation_runner.start_irrigation(
            station,
            minutes,
            source=source,
        )


    async def stop_irrigation(self):
        """Stop irrigation."""
        await self.irrigation_runner.stop_irrigation()

    
    async def turn_controller_on(self):
        """Turn irrigation controller on."""
        await self.irrigation_runner.turn_controller_on()
    
    async def turn_controller_off(self):
        """Turn irrigation controller off."""
        await self.irrigation_runner.turn_controller_off()


    async def async_set_schedule(self, new_schedule):
        """Replace irrigation schedule from the frontend card."""
        await self.schedule_state.set_schedule(new_schedule)

    
    async def initialize_schedule(self):
        """Initialize the schedule if not already set."""
        await self.schedule_state.initialize_schedule()

    # ----------------------------------------------------------------------------
    # Here we add some custom functions on our data coordinator to be called
    # from entity platforms to get access to the specific data they want.
    #
    # These will be specific to your api or yo may not need them at all
    # ----------------------------------------------------------------------------
    def get_device(self, device_id: int) -> dict[str, Any]:
        """Get a device entity from our api data."""
        try:
            return [
                devices for devices in self.data if devices["device_id"] == device_id
            ][0]
        except (TypeError, IndexError):
            # In this case if the device id does not exist you will get an IndexError.
            # If api did not return any data, you will get TypeError.
            return None

    def get_device_parameter(self, device_id: int, parameter: str) -> Any:
        """Get the parameter value of one of our devices from our api data."""
        if device := self.get_device(device_id):
            return device.get(parameter)
