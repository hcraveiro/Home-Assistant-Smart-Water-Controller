"""DataUpdateCoordinator for our integration."""

from datetime import datetime, timedelta
from homeassistant.util import slugify, dt as dt_util
import logging
import asyncio
from asyncio import sleep

from typing import Any
from homeassistant.helpers.storage import Store

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_SENSORS,
    CONF_SCAN_INTERVAL,
    CONF_NAME,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
)

from .util import mac_to_uuid, ensure_datetime, ensure_aware, parse_time_string, get_controller_service_prefix
from .models import IrrigationController, IrrigationStation
from .api import SmartWaterControllerAPI, WeatherAPI, APIConnectionError
from .const import (
    DEFAULT_SCAN_INTERVAL,
    CONTROLLER_MAC_ADDRESS,
    SERVICE_ACTIONS,
    NUM_STATIONS,
    WEATHER_API_KEY,
    SPRINKLE_WITH_RAIN,
    WEATHER_PROVIDER,
    WEATHER_PROVIDER_NONE,
    WEATHER_PROVIDER_OPENWEATHERMAP,
    WEATHER_PROVIDER_PIRATEWEATHER,
    BLUETOOTH_TIMEOUT,
    BLUETOOTH_MIN_TIMEOUT,
    BLUETOOTH_DEFAULT_TIMEOUT,
    WEATHER_API_CACHE_TIMEOUT,
    WEATHER_API_CACHE_DEFAULT_TIMEOUT,
    DOMAIN,
    IRRIGATION_CONTROL_METHOD,
    IRRIGATION_CONTROL_METHOD_SERVICE,
    IRRIGATION_CONTROL_METHOD_SWITCH,
    STATION_SWITCH_ENTITIES,
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

        # Set variables from values entered in config flow setup
        controller_mac_source = (
            (config_entry.unique_id or "").strip()
            or (config_entry.data.get(CONTROLLER_MAC_ADDRESS, "") or "").strip()
        )
        self._controller_mac_address = _extract_mac_address(controller_mac_source)
    
        _LOGGER.info(f"{self.controller_mac_address} - Starting Coordinator")
        
        # set variables from options.  You need a default here in case options have not been set
        self.poll_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        self.bluetooth_timeout = self.config_entry.options.get(
            BLUETOOTH_TIMEOUT, BLUETOOTH_DEFAULT_TIMEOUT
        )
        self.weather_api_timeout = self.config_entry.options.get(
            WEATHER_API_CACHE_TIMEOUT, WEATHER_API_CACHE_DEFAULT_TIMEOUT
        )

        self.sprinkle_with_rain = self.config_entry.data.get(SPRINKLE_WITH_RAIN, "false") == "true"
        self._configure_weather()

        self.soil_moisture_sensor = self.config_entry.data.get("soil_moisture_sensor")
        self.soil_moisture_threshold = float(self.config_entry.data.get("soil_moisture_threshold", 0))

        # Initialise DataUpdateCoordinator
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({config_entry.unique_id})",
            # Method to call on every update interval.
            update_method=self.async_update_data,
            # Polling interval. Will only be polled if you have made your
            # platform entities, CoordinatorEntities.
            # Using config option here but you can just use a fixed value.
            update_interval=timedelta(seconds=self.poll_interval),
        )

        self.num_stations = self.config_entry.data.get("num_stations", 2)
        self.station_areas = self.config_entry.data.get("station_areas", [0] * self.num_stations)
        if not isinstance(self.station_areas, list) or len(self.station_areas) != self.num_stations:
            _LOGGER.warning(f"{self.controller_mac_address} - station_areas missing or invalid, setting defaults.")
            self.station_areas = [0] * self.num_stations
        self.station_switch_entities = self.config_entry.data.get(STATION_SWITCH_ENTITIES, [])
        if not isinstance(self.station_switch_entities, list):
            self.station_switch_entities = []
            
        # Create instances of devices
        self.controller = IrrigationController(
            device_id=f"{self.controller_mac_address}_irrigation_controller_status",
            device_name="Controller Status",
            device_uid="",
            software_version="1.0",
            icon="mdi:state-machine",
        )

        # Station names are optional; if missing or invalid, fall back to "Station X".
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
            IRRIGATION_CONTROL_METHOD, IRRIGATION_CONTROL_METHOD_SERVICE
        )

        # Persisted active irrigation (used for switch method restart safety)
        self.active_irrigation: dict[str, Any] | None = None

        # self.weather_api is configured inside _configure_weather()

        self.storage = Store(hass, 1, f"irrigation_{config_entry.unique_id}")
        self.irrigation_stop_event = asyncio.Event()
        
        # ---- Default init for attributes to avoid race conditions ----
        self.schedule: list[dict[str, Any]] | None = None
        self.next_schedule: datetime | None = None
        self._watering_unsubscribers = []
        self._station_switch_unsubscribers = []
        
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
        
        self.irrigation_manual_duration = 10  # <-- este evita o erro atual
        self.water_flow_rate = [12] * self.num_stations
        self.sprinkle_total_amount_today = [0.0] * self.num_stations
        self.sprinkle_target_amount_today = [0.0] * self.num_stations
        
        # Total planned irrigation for today, based only on schedule, flow rate and area.
        self.forecasted_sprinkle_today = [0.0] * self.num_stations
        
        # Remaining irrigation still needed today, after applied water and expected rain.
        self.remaining_sprinkle_today = [0.0] * self.num_stations
        
        self.init_task = hass.async_create_task(self.async_init())
    
        _LOGGER.info(f"{self.controller_mac_address} - Coordinator initialization finished!")


    async def update_config(self, new_config: ConfigEntry):
        """Update the coordinator with new configuration."""
        
        _LOGGER.info(f"{self.controller_mac_address} - Updating Coordinator with new config...")
        self.config_entry = new_config  # Atualizar as configurações internas

        self._controller_mac_address = _extract_mac_address(
            self.config_entry.data.get(CONTROLLER_MAC_ADDRESS, "")
        )
        
        self.sprinkle_with_rain = self.config_entry.data.get(SPRINKLE_WITH_RAIN, "false") == "true"
        self._configure_weather()

        self.soil_moisture_sensor = self.config_entry.data.get("soil_moisture_sensor")
        
        try:
            self.soil_moisture_threshold = float(self.config_entry.data.get("soil_moisture_threshold", 0))
        except (TypeError, ValueError):
            self.soil_moisture_threshold = 0.0
            _LOGGER.warning(f"{self.controller_mac_address} - Invalid soil_moisture_threshold; using 0.0")

        # set variables from options.  You need a default here in case options have not been set
        self.poll_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        self.bluetooth_timeout = self.config_entry.options.get(
            BLUETOOTH_TIMEOUT, BLUETOOTH_DEFAULT_TIMEOUT
        )
        self.weather_api_timeout = self.config_entry.options.get(
            WEATHER_API_CACHE_TIMEOUT, WEATHER_API_CACHE_DEFAULT_TIMEOUT
        )

        # self.weather_api is configured inside _configure_weather()

        self.num_stations = self.config_entry.data.get("num_stations", 2)
        self.station_areas = self.config_entry.data.get("station_areas", [0] * self.num_stations)
        if not isinstance(self.station_areas, list) or len(self.station_areas) != self.num_stations:
            _LOGGER.warning(f"{self.controller_mac_address} - station_areas missing or invalid on update, setting defaults.")
            self.station_areas = [0] * self.num_stations
        # Station names are optional; if missing or invalid, fall back to "Station X".
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
                icon = "mdi:state-machine"
            )
            for station_id in range(1, self.num_stations + 1)
        ]
        # Fazer um refresh imediato com os novos dados
        await self.initialize_schedule()
        await self.setup_station_switch_tracking()
        await self.async_request_refresh()
        _LOGGER.info(f"{self.controller_mac_address} - Updated Coordinator with new config.")

    def _configure_weather(self) -> None:
        """Configure Weather integration based on selected provider."""
    
        provider = self.config_entry.data.get(WEATHER_PROVIDER, WEATHER_PROVIDER_NONE)
 
        self.weather_api_key = (self.config_entry.data.get(WEATHER_API_KEY) or "").strip()
    
        zone_entity_id = self.config_entry.data.get(CONF_SENSORS)
        zone_state = self.hass.states.get(zone_entity_id) if zone_entity_id else None
    
        self.latitude = None
        self.longitude = None
        if zone_state:
            self.latitude = zone_state.attributes.get("latitude")
            self.longitude = zone_state.attributes.get("longitude")
    
        if provider == WEATHER_PROVIDER_NONE:
            self.weather_api = None
            return
    
        if not self.weather_api_key or not self.latitude or not self.longitude:
            self.weather_api = None
            return
    
        # Let WeatherAPI pick the provider implementation.
        self.weather_api = WeatherAPI(
            self.weather_api_key,
            self.latitude,
            self.longitude,
            self.weather_api_timeout,
            provider=provider,
        )


    async def load_persistent_data(self):
        """Load persistent data from storage"""
        storage_data = await self.storage.async_load()

        if storage_data:
            self.will_it_rain_today = storage_data.get("will_it_rain_today")
            self.will_it_rain_today_forecast = storage_data.get("will_it_rain_today_forecast") or []
            if self.weather_api:
                self.weather_api._cache_forecast = self.will_it_rain_today_forecast
            self.has_rained_today = storage_data.get("has_rained_today")
            self.is_raining_now = storage_data.get("is_raining_now")
            self.is_raining_now_json = storage_data.get("is_raining_now_json") or {}
            if self.weather_api:
                self.weather_api._cache_current = self.is_raining_now_json
            self.irrigation_manual_duration = storage_data.get("irrigation_manual_duration")
            self.rain_time_today = storage_data.get("rain_time_today", 0)
            self.rain_total_amount_today = storage_data.get("rain_total_amount_today", 0)
            self.rain_total_amount_forecasted_today = storage_data.get("rain_total_amount_forecasted_today", 0)
            self.total_water_consumption = storage_data.get("total_water_consumption", 0)

            self.sprinkle_total_amount_today = storage_data.get("sprinkle_total_amount_today")
            if not isinstance(self.sprinkle_total_amount_today, list) or len(self.sprinkle_total_amount_today) != self.num_stations:
                _LOGGER.debug(f"{self.controller_mac_address} - Initializing sprinkle_total_amount_today with default values.")
                self.sprinkle_total_amount_today = [0.0] * self.num_stations

            self.sprinkle_target_amount_today = storage_data.get("sprinkle_target_amount_today")
            if not isinstance(self.sprinkle_target_amount_today, list) or len(self.sprinkle_target_amount_today) != self.num_stations:
                _LOGGER.debug(f"{self.controller_mac_address} - Initializing sprinkle_target_amount_today with default values.")
                self.sprinkle_target_amount_today = [0.0] * self.num_stations

            stored_forecasted_sprinkle_today = storage_data.get("forecasted_sprinkle_today")
            stored_remaining_sprinkle_today = storage_data.get("remaining_sprinkle_today")
            
            # Backward compatibility:
            # Older versions stored the remaining value inside forecasted_sprinkle_today.
            if isinstance(stored_remaining_sprinkle_today, list) and len(stored_remaining_sprinkle_today) == self.num_stations:
                self.remaining_sprinkle_today = stored_remaining_sprinkle_today
            elif isinstance(stored_forecasted_sprinkle_today, list) and len(stored_forecasted_sprinkle_today) == self.num_stations:
                _LOGGER.debug(
                    f"{self.controller_mac_address} - Migrating old forecasted_sprinkle_today values "
                    "to remaining_sprinkle_today."
                )
                self.remaining_sprinkle_today = stored_forecasted_sprinkle_today
            else:
                _LOGGER.debug(f"{self.controller_mac_address} - Initializing remaining_sprinkle_today with default values.")
                self.remaining_sprinkle_today = [0.0] * self.num_stations
            
            # New meaning: forecasted_sprinkle_today is the planned amount for the day.
            # If missing or invalid, it is restored from sprinkle_target_amount_today.
            if isinstance(stored_forecasted_sprinkle_today, list) and len(stored_forecasted_sprinkle_today) == self.num_stations:
                self.forecasted_sprinkle_today = list(self.sprinkle_target_amount_today)
            else:
                _LOGGER.debug(f"{self.controller_mac_address} - Initializing forecasted_sprinkle_today from target values.")
                self.forecasted_sprinkle_today = list(self.sprinkle_target_amount_today)

            self.schedule = storage_data.get("schedule")
            
            # Active irrigation is only used to protect switch-based watering from
            # being left on indefinitely after a Home Assistant restart.
            self.active_irrigation = storage_data.get("active_irrigation")

            self.water_flow_rate = storage_data.get("water_flow_rate")
            if not isinstance(self.water_flow_rate, list) or len(self.water_flow_rate) != self.num_stations:
                _LOGGER.debug(f"{self.controller_mac_address} - Initializing water_flow_rate with default values.")
                self.water_flow_rate = [12] * self.num_stations

            last_reset = storage_data.get("last_reset")
            if isinstance(last_reset, str):
                try:
                    self.last_reset = datetime.strptime(last_reset, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    _LOGGER.error(f"{self.controller_mac_address} - Invalid date format for last_reset: {last_reset}")
                    self.last_reset = dt_util.now()
            else:
                self.last_reset = last_reset or dt_util.now()

            last_rain = storage_data.get("last_rain")
            if isinstance(last_rain, str):
                try:
                    self.last_rain = datetime.strptime(last_rain, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    _LOGGER.error(f"{self.controller_mac_address} - Invalid date format for last_rain: {last_rain}")
                    self.last_rain = dt_util.now()
            else:
                self.last_rain = last_rain or dt_util.now()

            last_sprinkle = storage_data.get("last_sprinkle")
            if isinstance(last_sprinkle, str):
                try:
                    self.last_sprinkle = datetime.strptime(last_sprinkle, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    _LOGGER.error(f"{self.controller_mac_address} - Invalid date format for last_sprinkle: {last_sprinkle}")
                    self.last_sprinkle = dt_util.now()
            else:
                self.last_sprinkle = last_sprinkle or dt_util.now()

            # Normalize all datetimes to be aware
            self.last_reset = ensure_aware(self.last_reset)
            self.last_rain = ensure_aware(self.last_rain)
            self.last_sprinkle = ensure_aware(self.last_sprinkle)

        else:
            self.will_it_rain_today = False
            self.will_it_rain_today_forecast = []
            self.has_rained_today = False
            self.is_raining_now = False
            self.is_raining_now_json = []
            self.last_reset = dt_util.now()
            self.last_sprinkle = dt_util.now()
            self.last_rain = dt_util.now()
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
            
            self.schedule = None

        _LOGGER.info(f"{self.controller_mac_address} - Persistent data loaded.")

    async def save_persistent_data(self):
        """Save persistent data on storage."""

        if isinstance(self.last_reset, str):
            self.last_reset = datetime.fromisoformat(self.last_reset)
        if isinstance(self.last_rain, str):
            self.last_rain = datetime.fromisoformat(self.last_rain)
        if isinstance(self.last_sprinkle, str):
            self.last_sprinkle = datetime.fromisoformat(self.last_sprinkle)
        
        storage_data = {
            "will_it_rain_today": self.will_it_rain_today,
            "will_it_rain_today_forecast": self.will_it_rain_today_forecast,
            "has_rained_today": self.has_rained_today,
            "is_raining_now": self.is_raining_now,
            "is_raining_now_json": self.is_raining_now_json,
            "last_reset": ensure_aware(self.last_reset).strftime("%Y-%m-%d %H:%M:%S"),
            "last_sprinkle": ensure_aware(self.last_sprinkle or datetime.min).strftime("%Y-%m-%d %H:%M:%S"),
            "last_rain": ensure_aware(self.last_rain or datetime.min).strftime("%Y-%m-%d %H:%M:%S"),
            "irrigation_manual_duration": self.irrigation_manual_duration,
            "water_flow_rate": self.water_flow_rate,
            "rain_time_today": self.rain_time_today,
            "rain_total_amount_today": self.rain_total_amount_today,
            "rain_total_amount_forecasted_today": self.rain_total_amount_forecasted_today,
            "total_water_consumption": self.total_water_consumption,
            "sprinkle_total_amount_today": self.sprinkle_total_amount_today,
            "sprinkle_target_amount_today": self.sprinkle_target_amount_today,
            "forecasted_sprinkle_today": self.forecasted_sprinkle_today,
            "remaining_sprinkle_today": self.remaining_sprinkle_today,
            "schedule": self.schedule,
            "active_irrigation": self.active_irrigation,
        }
    
        await self.storage.async_save(storage_data)
        _LOGGER.debug(f"{self.controller_mac_address} - Persistent data saved.")

    async def setup_scheduled_tasks(self):
        """Create scheduled tasks."""
        _LOGGER.info(f"{self.controller_mac_address} - Scheduling tasks for midnight...")
    
        @callback
        def _run_daily_reset(_now) -> None:
            """Run the daily reset task."""
            self.hass.async_create_task(self.reset_rain_sprinkle_indicators())
    
        @callback
        def _run_daily_schedule_check(_now) -> None:
            """Run the daily watering schedule check."""
            self.hass.async_create_task(self.check_and_schedule_watering())
    
        async_track_time_change(
            self.hass,
            _run_daily_reset,
            hour=0,
            minute=0,
            second=0,
        )
    
        async_track_time_change(
            self.hass,
            _run_daily_schedule_check,
            hour=0,
            minute=1,
            second=0,
        )
    
        _LOGGER.info(f"{self.controller_mac_address} - Scheduled tasks.")

    async def async_init(self):
        await self.load_persistent_data()
    
        # If we were watering using switch-based control when HA restarted,
        # ensure the station is not left running indefinitely.
        await self._async_restore_active_irrigation()
        
        """Init APIs and schedule tasks."""
    
        _LOGGER.info(f"{self.controller_mac_address} - Connecting to SmartWaterController API...")
        try:
            await self.api.connect()
            _LOGGER.info(f"{self.controller_mac_address} - Connected to SmartWaterController API")
        except Exception as ex:
            _LOGGER.warning(f"{self.controller_mac_address} - Failed connecting to SmartWaterController device ({self.controller_mac_address})!, ex={ex}")
    
        await self.initialize_schedule()
        await self.setup_station_switch_tracking()
        
        # Executa imediatamente após inicialização
        await self.check_and_schedule_watering()
        await self.setup_scheduled_tasks()
        self.data = await self.async_update_all_sensors()

    def _is_switch_control_method(self) -> bool:
        """Return True if this config entry uses switch-based control."""
        return self.irrigation_control_method == IRRIGATION_CONTROL_METHOD_SWITCH
    
    async def _async_restore_active_irrigation(self) -> None:
        """Restore active irrigation safety for switch control method.
    
        When using the 'switch' control method, watering is implemented by
        turning a station switch on and later turning it off. If Home Assistant
        restarts mid-watering, we must ensure the switch is turned off at the
        expected end time to avoid indefinite watering.
        """
        if not self._is_switch_control_method():
            self.active_irrigation = None
            return
    
        if not isinstance(self.active_irrigation, dict):
            self.active_irrigation = None
            return
    
        station = self.active_irrigation.get("station")
        end_at_str = self.active_irrigation.get("end_at")
        if not station or not end_at_str:
            self.active_irrigation = None
            return
    
        try:
            end_at = datetime.fromisoformat(end_at_str)
            end_at = ensure_aware(end_at)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.warning("%s - Invalid active irrigation end_at: %s", self.controller_mac_address, end_at_str)
            self.active_irrigation = None
            return
    
        now = dt_util.now()
        if now >= end_at:
            _LOGGER.warning(
                "%s - Found stale active irrigation in storage (station %s). Turning off now.",
                self.controller_mac_address,
                station,
            )
            try:
                await self.api.turn_off_station_switch(int(station))
            except Exception:  # pylint: disable=broad-except
                _LOGGER.warning("%s - Failed turning off stale station switch", self.controller_mac_address, exc_info=True)
    
            self.active_irrigation = None
            await self.save_persistent_data()
            return
    
        remaining_seconds = int((end_at - now).total_seconds())
        if remaining_seconds <= 0:
            self.active_irrigation = None
            await self.save_persistent_data()
            return
    
        _LOGGER.info(
            "%s - Restored active irrigation (station %s). Will stop in %s seconds.",
            self.controller_mac_address,
            station,
            remaining_seconds,
        )
    
        # Reflect state in entities (best-effort). The actual switch might already be ON.
        try:
            self.stations[int(station) - 1].state = "Sprinkling"
        except Exception:  # pylint: disable=broad-except
            pass
    
        # Schedule an automatic stop at the expected end time.
        self.hass.create_task(self._async_stop_irrigation_after_delay(int(station), remaining_seconds))

    async def _async_stop_irrigation_after_delay(self, station: int, delay_seconds: int) -> None:
        """Stop irrigation after a delay (used to protect switch method on restart)."""
        try:
            await asyncio.sleep(delay_seconds)
            # Only stop if this is still the active irrigation.
            if isinstance(self.active_irrigation, dict) and int(self.active_irrigation.get("station", -1)) == int(station):
                await self.stop_irrigation()
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("%s - Error while scheduling irrigation auto-stop", self.controller_mac_address)

    def _clear_station_switch_callbacks(self) -> None:
        """Cancel all currently registered station switch callbacks."""
        for unsubscribe in list(getattr(self, "_station_switch_unsubscribers", [])):
            try:
                unsubscribe()
            except Exception:  # pylint: disable=broad-except
                _LOGGER.debug(
                    f"{self.controller_mac_address} - Failed to cancel station switch callback.",
                    exc_info=True,
                )
    
        self._station_switch_unsubscribers = []


    def _get_station_switch_entity_id(self, station_id: int) -> str | None:
        """Return the switch entity configured for a station."""
        if not self._is_switch_control_method():
            return None
    
        if not isinstance(self.station_switch_entities, list):
            return None
    
        index = station_id - 1
    
        if index < 0 or index >= len(self.station_switch_entities):
            return None
    
        entity_id = self.station_switch_entities[index]
    
        if not entity_id:
            return None
    
        return str(entity_id)


    async def setup_station_switch_tracking(self) -> None:
        """Track station switch changes when using switch-based irrigation control."""
        self._clear_station_switch_callbacks()
    
        if not self._is_switch_control_method():
            _LOGGER.debug(f"{self.controller_mac_address} - Station switch tracking disabled because control method is not switch.")
            return
    
        if not self.station_switch_entities:
            _LOGGER.warning(f"{self.controller_mac_address} - Switch control method is enabled but no station switches are configured.")
            return
    
        for station_id in range(1, self.num_stations + 1):
            entity_id = self._get_station_switch_entity_id(station_id)
    
            if not entity_id:
                _LOGGER.warning(
                    f"{self.controller_mac_address} - No switch entity configured for station {station_id}."
                )
                continue
    
            @callback
            def _handle_station_switch_change(event, tracked_station_id=station_id, tracked_entity_id=entity_id) -> None:
                """Handle station switch state changes."""
                new_state = event.data.get("new_state")
                old_state = event.data.get("old_state")
    
                if new_state is None:
                    return
    
                old_value = old_state.state if old_state else None
                new_value = new_state.state
    
                _LOGGER.debug(
                    f"{self.controller_mac_address} - Station {tracked_station_id} switch changed: "
                    f"{tracked_entity_id} {old_value} -> {new_value}"
                )
    
                self.hass.async_create_task(
                    self._async_sync_station_states_from_switches()
                )
    
            unsubscribe = async_track_state_change_event(
                self.hass,
                [entity_id],
                _handle_station_switch_change,
            )
    
            self._station_switch_unsubscribers.append(unsubscribe)
    
            _LOGGER.info(
                f"{self.controller_mac_address} - Tracking station {station_id} switch: {entity_id}"
            )
    
        await self._async_sync_station_states_from_switches()


    async def _async_sync_station_states_from_switches(self) -> None:
        """Synchronize station status entities from configured switch states."""
        if not self._is_switch_control_method():
            return
    
        changed = False
    
        for station_id in range(1, self.num_stations + 1):
            entity_id = self._get_station_switch_entity_id(station_id)
    
            if not entity_id:
                continue
    
            state = self.hass.states.get(entity_id)
    
            if state is None:
                _LOGGER.debug(
                    f"{self.controller_mac_address} - Station {station_id} switch entity not found: {entity_id}"
                )
                continue
    
            if state.state == STATE_ON:
                new_station_state = "Sprinkling"
            elif state.state == STATE_OFF:
                new_station_state = "Stopped"
            else:
                _LOGGER.debug(
                    f"{self.controller_mac_address} - Station {station_id} switch has unsupported state: "
                    f"{entity_id}={state.state}"
                )
                continue
    
            if self.stations[station_id - 1].state != new_station_state:
                self.stations[station_id - 1].state = new_station_state
                changed = True
    
                _LOGGER.info(
                    f"{self.controller_mac_address} - Station {station_id} state synchronized from switch "
                    f"{entity_id}: {new_station_state}"
                )
    
        if isinstance(self.active_irrigation, dict):
            active_station = self.active_irrigation.get("station")
    
            try:
                active_station_id = int(active_station)
            except (TypeError, ValueError):
                active_station_id = None
    
            if active_station_id:
                active_switch_entity_id = self._get_station_switch_entity_id(active_station_id)
                active_switch_state = self.hass.states.get(active_switch_entity_id) if active_switch_entity_id else None
    
                if active_switch_state and active_switch_state.state == STATE_OFF:
                    _LOGGER.info(
                        f"{self.controller_mac_address} - Active irrigation station {active_station_id} is now off. "
                        "Clearing persisted active irrigation."
                    )
                    self.active_irrigation = None
                    await self.save_persistent_data()
    
        if changed:
            data = await self.async_update_all_sensors()
    
            if data is not None:
                self.async_set_updated_data(data)
            else:
                _LOGGER.warning(
                    f"{self.controller_mac_address} - async_update_all_sensors() returned None after switch state sync."
                )
    
    def _clear_scheduled_watering_callbacks(self) -> None:
        """Cancel all currently scheduled watering callbacks."""
        for unsubscribe in list(getattr(self, "_watering_unsubscribers", [])):
            try:
                unsubscribe()
            except Exception:  # pylint: disable=broad-except
                _LOGGER.debug(
                    f"{self.controller_mac_address} - Failed to cancel a scheduled watering callback.",
                    exc_info=True,
                )
    
        self._watering_unsubscribers = []
    
    
    def _get_valid_watering_hours(self, month_config: dict[str, Any]) -> list[str]:
        """Return valid watering hours sorted by time."""
        valid_hours: list[str] = []
    
        for hour in month_config.get("hours", []) or []:
            if not hour:
                continue
    
            try:
                parse_time_string(hour)
                valid_hours.append(hour)
            except ValueError:
                _LOGGER.error(f"{self.controller_mac_address} - Invalid hour format: {hour}")
    
        return sorted(valid_hours, key=lambda value: parse_time_string(value))
    
    
    def _get_future_watering_slots_for_day(self, day, month_config: dict[str, Any]) -> list[tuple[str, datetime]]:
        """Return future watering slots for the provided day."""
        now = dt_util.now()
        future_slots: list[tuple[str, datetime]] = []
    
        for hour in self._get_valid_watering_hours(month_config):
            watering_datetime = dt_util.as_local(datetime.combine(day, parse_time_string(hour)))
    
            if watering_datetime > now:
                future_slots.append((hour, watering_datetime))
    
        return future_slots
    
    
    def _latest_rain_or_sprinkle_event(self) -> datetime | None:
        """Return the latest rain or sprinkle event."""
        last_rain = ensure_aware(self.last_rain)
        last_sprinkle = ensure_aware(self.last_sprinkle)
    
        events = [event for event in (last_rain, last_sprinkle) if event is not None]
    
        if not events:
            return None
    
        return max(events)
    
    
    def _recent_event_blocks_today(self, interval_days: int) -> bool:
        """Check if a previous event should block watering today.
    
        Events from today do not block today's remaining watering slots.
        Rain amount and expected rain amount are handled by the remaining water calculation.
        """
        if interval_days <= 0:
            return False
    
        today = dt_util.now().date()
        latest_event = self._latest_rain_or_sprinkle_event()
    
        if latest_event is None:
            return False
    
        latest_event = ensure_aware(latest_event)
    
        if latest_event.date() == today:
            return False
    
        days_since_last_event = (today - latest_event.date()).days
    
        if days_since_last_event >= interval_days:
            return False
    
        _LOGGER.info(
            f"{self.controller_mac_address} - Last event was {days_since_last_event} days ago. "
            f"Interval of {interval_days} days not yet passed."
        )
    
        return True
    
    
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
        target = [0.0] * self.num_stations
    
        if not self.schedule:
            _LOGGER.debug(f"{self.controller_mac_address} - Schedule not initialized. Target amounts: {target}")
            return target
    
        today = dt_util.now().date()
        current_month_index = today.month - 1
    
        month_config = self.schedule[current_month_index]
    
        if not month_config:
            _LOGGER.debug(f"{self.controller_mac_address} - No month configuration. Target amounts: {target}")
            return target
    
        interval_days = month_config.get("interval_days", 2)
    
        if not only_future_slots and self._recent_event_blocks_today(interval_days):
            _LOGGER.debug(f"{self.controller_mac_address} - Recent event blocks today. Target amounts: {target}")
            return target
    
        watering_hours = self._get_valid_watering_hours(month_config)
    
        if only_future_slots:
            future_slots = self._get_future_watering_slots_for_day(today, month_config)
            watering_hours = [hour for hour, _watering_datetime in future_slots]
    
        if not watering_hours:
            if include_already_applied:
                target = [
                    round(float(self.sprinkle_total_amount_today[station_id - 1]), 2)
                    for station_id in range(1, self.num_stations + 1)
                ]
    
            _LOGGER.debug(f"{self.controller_mac_address} - No watering hours. Target amounts: {target}")
            return target
    
        occurrences = len(watering_hours)
        stations = month_config.get("stations", {})
    
        for station_id in range(1, self.num_stations + 1):
            key = f"station_{station_id}_minutes"
    
            try:
                minutes = int(stations.get(key, 0))
            except (TypeError, ValueError):
                _LOGGER.warning(
                    f"{self.controller_mac_address} - Invalid scheduled minutes for station {station_id}: "
                    f"{stations.get(key, 0)}"
                )
                minutes = 0
    
            already_applied = 0.0
    
            if include_already_applied:
                already_applied = float(self.sprinkle_total_amount_today[station_id - 1])
    
            if minutes <= 0:
                target[station_id - 1] = round(already_applied, 2)
                continue
    
            flow = self.water_flow_rate[station_id - 1]
            area = self.station_areas[station_id - 1] or 1
            scheduled_mm = (flow / area) * minutes * occurrences
    
            target[station_id - 1] = round(already_applied + scheduled_mm, 2)
    
        _LOGGER.debug(
            f"{self.controller_mac_address} - Sprinkle target amounts: {target} "
            f"only_future_slots={only_future_slots}, include_already_applied={include_already_applied}"
        )
    
        return target


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
        for station_id in range(1, self.num_stations + 1):
            target_mm = self.sprinkle_target_amount_today[station_id - 1]
            applied_mm = self.sprinkle_total_amount_today[station_id - 1]
            rain_mm = self.rain_total_amount_forecasted_today
    
            remaining = target_mm - (applied_mm + rain_mm)
            if remaining > 0:
                _LOGGER.debug(
                    f"{self.controller_mac_address} - Station {station_id} needs more water: "
                    f"Target={target_mm}mm, Applied={applied_mm}mm, Rain={rain_mm}mm → Remaining={remaining}mm"
                )
                return True  # Pelo menos uma estação ainda precisa de rega
    
        _LOGGER.debug(f"{self.controller_mac_address} - All stations have enough water today.")
        return False


    async def check_and_schedule_watering(self, *_):
        """Check if watering can run today and schedule future watering slots."""
        _LOGGER.info(f"{self.controller_mac_address} - Checking and scheduling watering times...")
    
        self._clear_scheduled_watering_callbacks()
    
        if not self.schedule:
            _LOGGER.warning(f"{self.controller_mac_address} - Schedule not initialized, skipping watering check.")
            return
    
        now = dt_util.now()
        today = now.date()
        current_month_index = today.month - 1
    
        month_config = self.schedule[current_month_index]
    
        if not month_config:
            _LOGGER.info(f"{self.controller_mac_address} - No configuration active for this month.")
            return
    
        interval_days = month_config.get("interval_days", 2)
    
        if self._recent_event_blocks_today(interval_days):
            return
    
        future_slots = self._get_future_watering_slots_for_day(today, month_config)
    
        if not future_slots:
            _LOGGER.info(f"{self.controller_mac_address} - No remaining watering slots for today.")
            return
    
        if not self.needs_watering_today():
            _LOGGER.info(
                f"{self.controller_mac_address} - No station currently needs watering, "
                "but future watering slots will still be scheduled because the forecast may change."
            )
    
        for hour, watering_datetime in future_slots:
            delay = (watering_datetime - now).total_seconds()
    
            if delay <= 0:
                continue
    
            @callback
            def _run_scheduled_watering(_now, scheduled_hour=hour) -> None:
                """Run a scheduled watering slot."""
                self.hass.async_create_task(
                    self.run_watering_cycle(scheduled_hour=scheduled_hour)
                )
    
            unsubscribe = async_call_later(
                self.hass,
                delay,
                _run_scheduled_watering,
            )
    
            self._watering_unsubscribers.append(unsubscribe)
    
            _LOGGER.info(
                f"{self.controller_mac_address} - Watering slot scheduled for {watering_datetime} "
                f"(scheduled slot: {hour})"
            )
    
        _LOGGER.debug(f"{self.controller_mac_address} - Scheduled watering slots.")
        

    async def get_next_watering_date(self) -> datetime | None:
        """Get the next watering time considering today's remaining slots."""
        _LOGGER.debug(f"{self.controller_mac_address} - Determining next watering schedule...")
    
        if not self.schedule:
            _LOGGER.debug(f"{self.controller_mac_address} - Schedule not initialized yet.")
            return None
    
        today = dt_util.now().date()
        current_month_index = today.month - 1
    
        month_config = self.schedule[current_month_index]
    
        if month_config:
            interval_days = month_config.get("interval_days", 2)
            future_slots_today = self._get_future_watering_slots_for_day(today, month_config)
    
            if (
                future_slots_today
                and not self._recent_event_blocks_today(interval_days)
                and self.needs_watering_today()
            ):
                next_datetime = future_slots_today[0][1]
                _LOGGER.debug(f"{self.controller_mac_address} - Next watering is today: {next_datetime}")
                return next_datetime
    
        latest_event = self._latest_rain_or_sprinkle_event()
    
        if latest_event:
            latest_event = ensure_aware(latest_event)
            event_month_config = self.schedule[latest_event.date().month - 1]
            interval_days = event_month_config.get("interval_days", 2) if event_month_config else 1
            next_watering_day = latest_event.date() + timedelta(days=max(1, interval_days))
        else:
            next_watering_day = today + timedelta(days=1)
    
        if next_watering_day <= today:
            next_watering_day = today + timedelta(days=1)
    
        for _ in range(370):
            day_config = self.schedule[next_watering_day.month - 1]
    
            if day_config:
                watering_hours = self._get_valid_watering_hours(day_config)
    
                if watering_hours:
                    next_datetime = dt_util.as_local(
                        datetime.combine(next_watering_day, parse_time_string(watering_hours[0]))
                    )
    
                    _LOGGER.debug(
                        f"{self.controller_mac_address} - Next watering schedule determined: {next_datetime}"
                    )
    
                    return next_datetime
    
            next_watering_day += timedelta(days=1)
    
        _LOGGER.debug(f"{self.controller_mac_address} - No watering schedule found.")
        return None

    async def run_watering_cycle(self, *_, scheduled_hour: str | None = None):
        """Run the scheduled watering cycle if all conditions are met."""
        _LOGGER.info(f"{self.controller_mac_address} - Running scheduled watering cycle...")
    
        if self.soil_moisture_sensor:
            state = self.hass.states.get(self.soil_moisture_sensor)
    
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    moisture = float(state.state)
    
                    if moisture >= self.soil_moisture_threshold:
                        _LOGGER.info(
                            f"{self.controller_mac_address} - Soil moisture is {moisture}%, "
                            f"above threshold ({self.soil_moisture_threshold}%). Skipping watering."
                        )
                        return
    
                    _LOGGER.debug(
                        f"{self.controller_mac_address} - Soil moisture is {moisture}%, "
                        f"below threshold ({self.soil_moisture_threshold}%). Proceeding with watering."
                    )
                except ValueError:
                    _LOGGER.warning(
                        f"{self.controller_mac_address} - Failed to parse soil moisture value: {state.state}"
                    )
            else:
                _LOGGER.warning(
                    f"{self.controller_mac_address} - Soil moisture sensor state is unknown or unavailable: "
                    f"{state.state if state else 'None'}"
                )
    
        if self._rain_blocks_watering_now():
            _LOGGER.info(f"{self.controller_mac_address} - Watering skipped because it is raining now.")
            return
    
        current_month_index = dt_util.now().month - 1
        month_config = self.schedule[current_month_index]
    
        if not month_config:
            _LOGGER.info(f"{self.controller_mac_address} - No configuration active for this month.")
            return
    
        stations = month_config.get("stations", {})
        watering_hours = self._get_valid_watering_hours(month_config)
    
        if not watering_hours:
            _LOGGER.info(f"{self.controller_mac_address} - No watering hours configured for this month.")
            return
    
        if scheduled_hour and scheduled_hour not in watering_hours:
            _LOGGER.info(
                f"{self.controller_mac_address} - Scheduled slot {scheduled_hour} is no longer in the active schedule. "
                "Skipping stale watering callback."
            )
            return
    
        now = dt_util.now()
        reference_time = now.time()
    
        if scheduled_hour:
            try:
                reference_time = parse_time_string(scheduled_hour)
            except ValueError:
                _LOGGER.error(
                    f"{self.controller_mac_address} - Invalid scheduled hour received: {scheduled_hour}"
                )
    
        reference_hm = (reference_time.hour, reference_time.minute)
    
        current_slot_index = 0
    
        for hour in watering_hours:
            try:
                watering_time = parse_time_string(hour)
    
                if (watering_time.hour, watering_time.minute) <= reference_hm:
                    current_slot_index += 1
            except ValueError:
                _LOGGER.error(f"{self.controller_mac_address} - Invalid hour format: {hour}")
    
        current_slot_index = max(1, current_slot_index)
        total_slots_today = len(watering_hours)
    
        _LOGGER.debug(
            f"{self.controller_mac_address} - Hours={watering_hours} "
            f"scheduled_hour={scheduled_hour} reference={reference_hm} "
            f"current_slot_index={current_slot_index} total_slots_today={total_slots_today}"
        )
    
        for station_key, scheduled_minutes in stations.items():
            try:
                scheduled_minutes = int(scheduled_minutes)
            except (TypeError, ValueError):
                _LOGGER.warning(
                    f"{self.controller_mac_address} - Invalid scheduled minutes for {station_key}: "
                    f"{scheduled_minutes}"
                )
                continue
    
            if scheduled_minutes <= 0:
                continue
    
            station_id = int(station_key.replace("station_", "").replace("_minutes", ""))
    
            flow_rate = self.water_flow_rate[station_id - 1]
            area = self.station_areas[station_id - 1] or 1
            mm_per_minute = flow_rate / area
    
            if mm_per_minute <= 0:
                _LOGGER.warning(
                    f"{self.controller_mac_address} - Invalid mm per minute for station {station_id}: "
                    f"flow_rate={flow_rate}, area={area}"
                )
                continue
    
            planned_per_slot_mm = scheduled_minutes * mm_per_minute
            planned_until_this_slot_mm = planned_per_slot_mm * current_slot_index
    
            already_applied_mm = self.sprinkle_total_amount_today[station_id - 1]
            expected_rain_today_mm = self.rain_total_amount_forecasted_today
    
            amount_to_apply_now_mm = max(
                0.0,
                planned_until_this_slot_mm - (already_applied_mm + expected_rain_today_mm),
            )
    
            if amount_to_apply_now_mm <= 0:
                _LOGGER.info(
                    f"{self.controller_mac_address} - Station {station_id} does not need watering for this slot. "
                    f"PlannedUntilSlot={planned_until_this_slot_mm:.2f}mm, "
                    f"Applied={already_applied_mm:.2f}mm, "
                    f"ExpectedRain={expected_rain_today_mm:.2f}mm"
                )
                continue
    
            minutes_needed = int((amount_to_apply_now_mm / mm_per_minute) + 0.999)
    
            if minutes_needed > 0:
                _LOGGER.info(
                    f"{self.controller_mac_address} - Station {station_id} will irrigate for {minutes_needed} min "
                    f"to apply {amount_to_apply_now_mm:.2f}mm "
                    f"(slot={current_slot_index}/{total_slots_today}, "
                    f"planned_per_slot={planned_per_slot_mm:.2f}mm, "
                    f"planned_until_slot={planned_until_this_slot_mm:.2f}mm, "
                    f"applied={already_applied_mm:.2f}mm, "
                    f"expected_rain={expected_rain_today_mm:.2f}mm, "
                    f"mm/min={mm_per_minute:.2f})"
                )
    
                await self.start_irrigation(station_id, minutes_needed)

    async def async_update_all_sensors(self):
        _LOGGER.debug(f"{self.controller_mac_address} - Updating all sensors...")
    
        def _stable_uid(device_id: str) -> str:
            """Return a stable unique id for an entity regardless of sensor ordering."""
            return f"{self.controller_unique_prefix}_{device_id}"
    
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
    
        # Verifies if it's after 00:05:00
        now = dt_util.now()
        if now.time() > datetime.strptime("00:05:00", "%H:%M:%S").time():
            try:
                self.last_reset = ensure_datetime(self.last_reset)
                if self.last_reset.date() != now.date():
                    _LOGGER.info(
                        f"{self.controller_mac_address} - Last reset was on {self.last_reset.date()}, performing daily reset."
                    )
                    await self.reset_rain_sprinkle_indicators()
            except Exception as e:
                _LOGGER.warning(
                    f"{self.controller_mac_address} - Could not determine last_reset date. Skipping reset. Error: {e}"
                )
    
        data = []
    
        if self.weather_api:
            will_it_rain_result = await self.weather_api.will_it_rain()
            self.will_it_rain_today = will_it_rain_result.get("will_rain", False)
            self.will_it_rain_today_forecast = will_it_rain_result.get("forecast", [])
            is_raining_result = await self.weather_api.is_raining()
            self.is_raining_now = is_raining_result["is_raining"]
            self.is_raining_now_json = is_raining_result["current"]
        else:
            self.will_it_rain_today = False
            self.will_it_rain_today_forecast = []
            self.is_raining_now = False
            self.is_raining_now_json = {}
    
        if self.is_raining_now:
            self.has_rained_today = True
            self.last_rain = dt_util.now()
            self.rain_time_today += self.poll_interval / 60
            self.rain_total_amount_today += await self.calculate_rain_amount()
    
            if not self.sprinkle_with_rain:
                for station_id in range(1, self.num_stations + 1):
                    if self.stations[station_id - 1].state == "Sprinkling":
                        await self.stop_irrigation()
                        break
    
        if self.weather_api:
            self.rain_total_amount_forecasted_today = (
                await self.weather_api.get_total_rain_forecast_for_today()
            ) + self.rain_total_amount_today
        else:
            self.rain_total_amount_forecasted_today = self.rain_total_amount_today
            
        self.forecasted_sprinkle_today = [
            self.calculate_forecasted_sprinkle_today(station_id)
            for station_id in range(1, self.num_stations + 1)
        ]
        
        self.remaining_sprinkle_today = [
            self.calculate_remaining_sprinkle_today(station_id)
            for station_id in range(1, self.num_stations + 1)
        ]
    
        self.next_schedule = await self.get_next_watering_date()
    
        # Controller
        controller_device_id = self.controller.device_id
        data.append(
            {
                "device_id": controller_device_id,
                "device_type": "STATE_SENSOR",
                "device_name": self.controller.device_name,
                "device_uid": _stable_uid(controller_device_id),
                "software_version": self.controller.software_version,
                "state": self.controller.state,
                "icon": self.controller.icon,
                "last_reboot": self.controller.last_reboot,
            }
        )
    
        # Stations
        for station_id in range(1, self.num_stations + 1):
            station_device_id = self.stations[station_id - 1].device_id
            data.append(
                {
                    "device_id": station_device_id,
                    "device_type": "STATE_SENSOR",
                    "device_name": self.stations[station_id - 1].device_name,
                    "device_uid": _stable_uid(station_device_id),
                    "software_version": self.stations[station_id - 1].software_version,
                    "state": self.stations[station_id - 1].state,
                    "icon": self.stations[station_id - 1].icon,
                    "last_reboot": self.stations[station_id - 1].last_reboot,
                }
            )
    
        # Configurations
        manual_duration_device_id = f"{self.controller_mac_address}_irrigation_manual_duration"
        data.append(
            {
                "device_id": manual_duration_device_id,
                "device_type": "IRRIGATION_DURATION_NUMBER",
                "device_name": "Irrigation Manual Duration",
                "device_uid": _stable_uid(manual_duration_device_id),
                "software_version": "1.0",
                "value": self.irrigation_manual_duration,
                "icon": "mdi:clock-time-five-outline",
                "last_reboot": None,
            }
        )
    
        # Water flow numbers per station
        for station_id in range(1, self.num_stations + 1):
            station_label = (
                self.station_names[station_id - 1]
                if isinstance(getattr(self, "station_names", None), list)
                and len(self.station_names) >= station_id
                else f"Station {station_id}"
            )
    
            water_flow_device_id = f"{self.controller_mac_address}_water_flow_rate_{station_id}"
            data.append(
                {
                    "device_id": water_flow_device_id,
                    "device_type": "WATER_FLOW_NUMBER",
                    "device_name": f"Water Flow Rate {station_label}",
                    "device_uid": _stable_uid(water_flow_device_id),
                    "software_version": "1.0",
                    "value": self.water_flow_rate[station_id - 1],
                    "icon": "mdi:water-pump",
                    "last_reboot": None,
                }
            )
    
        # Buttons
        for station_id in range(1, self.num_stations + 1):
            station_label = (
                self.station_names[station_id - 1]
                if isinstance(getattr(self, "station_names", None), list)
                and len(self.station_names) >= station_id
                else f"Station {station_id}"
            )
    
            sprinkle_button_device_id = (
                f"{self.controller_mac_address}_irrigation_manual_start_station_{station_id}"
            )
            data.append(
                {
                    "device_id": sprinkle_button_device_id,
                    "device_type": "SPRINKLE_BUTTON",
                    "device_name": f"Sprinkle {station_label}",
                    "device_uid": _stable_uid(sprinkle_button_device_id),
                    "software_version": "1.0",
                    "icon": "mdi:sprinkler",
                    "last_reboot": None,
                }
            )
    
        # Sprinkle total amount (mm) per station
        for station_id in range(1, self.num_stations + 1):
            station_label = (
                self.station_names[station_id - 1]
                if isinstance(getattr(self, "station_names", None), list)
                and len(self.station_names) >= station_id
                else f"Station {station_id}"
            )
    
            sprinkle_total_device_id = (
                f"{self.controller_mac_address}_sprinkle_total_amount_today_station_{station_id}"
            )
            data.append(
                {
                    "device_id": sprinkle_total_device_id,
                    "device_type": "SPRINKLE_TOTAL_AMOUNT_SENSOR",
                    "device_name": f"Sprinkle Total Amount Today {station_label}",
                    "device_uid": _stable_uid(sprinkle_total_device_id),
                    "software_version": "1.0",
                    "state": round(self.sprinkle_total_amount_today[station_id - 1], 2),
                    "icon": "mdi:water",
                    "last_reboot": None,
                }
            )
    
        # Forecasted sprinkle today (mm) per station
        for station_id in range(1, self.num_stations + 1):
            station_label = (
                self.station_names[station_id - 1]
                if isinstance(getattr(self, "station_names", None), list)
                and len(self.station_names) >= station_id
                else f"Station {station_id}"
            )
        
            forecasted_sprinkle_device_id = (
                f"{self.controller_mac_address}_forecasted_sprinkle_today_station_{station_id}"
            )
            data.append(
                {
                    "device_id": forecasted_sprinkle_device_id,
                    "device_type": "FORECASTED_SPRINKLE_TODAY_SENSOR",
                    "device_name": f"Forecasted Sprinkle Today {station_label}",
                    "device_uid": _stable_uid(forecasted_sprinkle_device_id),
                    "software_version": "1.0",
                    "state": round(self.forecasted_sprinkle_today[station_id - 1], 2),
                    "icon": "mdi:water-check",
                    "last_reboot": None,
                }
            )
        
        # Remaining sprinkle today (mm) per station
        for station_id in range(1, self.num_stations + 1):
            station_label = (
                self.station_names[station_id - 1]
                if isinstance(getattr(self, "station_names", None), list)
                and len(self.station_names) >= station_id
                else f"Station {station_id}"
            )
        
            remaining_sprinkle_device_id = (
                f"{self.controller_mac_address}_remaining_sprinkle_today_station_{station_id}"
            )
            data.append(
                {
                    "device_id": remaining_sprinkle_device_id,
                    "device_type": "REMAINING_SPRINKLE_TODAY_SENSOR",
                    "device_name": f"Remaining Sprinkle Today {station_label}",
                    "device_uid": _stable_uid(remaining_sprinkle_device_id),
                    "software_version": "1.0",
                    "state": round(self.remaining_sprinkle_today[station_id - 1], 2),
                    "icon": "mdi:water-sync",
                    "last_reboot": None,
                }
            )
    
        stop_device_id = f"{self.controller_mac_address}_irrigation_stop"
        data.append(
            {
                "device_id": stop_device_id,
                "device_type": "STOP_BUTTON",
                "device_name": "Stop sprinkle",
                "device_uid": _stable_uid(stop_device_id),
                "software_version": "1.0",
                "icon": "mdi:water-off",
                "last_reboot": None,
            }
        )
    
        on_device_id = f"{self.controller_mac_address}_irrigation_controller_on"
        data.append(
            {
                "device_id": on_device_id,
                "device_type": "ON_BUTTON",
                "device_name": "Turn on controller",
                "device_uid": _stable_uid(on_device_id),
                "software_version": "1.0",
                "icon": "mdi:power-on",
                "last_reboot": None,
            }
        )
    
        off_device_id = f"{self.controller_mac_address}_irrigation_controller_off"
        data.append(
            {
                "device_id": off_device_id,
                "device_type": "OFF_BUTTON",
                "device_name": "Turn off controller",
                "device_uid": _stable_uid(off_device_id),
                "software_version": "1.0",
                "icon": "mdi:power-off",
                "last_reboot": None,
            }
        )
    
        # Rain-related devices are only created when Weather is enabled
        if self.weather_api:
            will_rain_device_id = f"{self.controller_mac_address}_will_rain_today"
            data.append(
                {
                    "device_id": will_rain_device_id,
                    "device_type": "WILL_RAIN_SENSOR",
                    "device_name": "Will it rain today",
                    "device_uid": _stable_uid(will_rain_device_id),
                    "software_version": "1.0",
                    "state": self.will_it_rain_today,
                    "icon": "mdi:weather-rainy",
                    "last_reboot": None,
                }
            )
    
            has_rained_device_id = f"{self.controller_mac_address}_has_rained_today"
            data.append(
                {
                    "device_id": has_rained_device_id,
                    "device_type": "HAS_RAINED_SENSOR",
                    "device_name": "Has rained today",
                    "device_uid": _stable_uid(has_rained_device_id),
                    "software_version": "1.0",
                    "state": self.has_rained_today,
                    "icon": "mdi:weather-rainy",
                    "last_reboot": None,
                }
            )
    
            is_raining_device_id = f"{self.controller_mac_address}_is_raining_now"
            data.append(
                {
                    "device_id": is_raining_device_id,
                    "device_type": "IS_RAINING_SENSOR",
                    "device_name": "Is it raining now",
                    "device_uid": _stable_uid(is_raining_device_id),
                    "software_version": "1.0",
                    "state": self.is_raining_now,
                    "icon": "mdi:weather-pouring",
                    "last_reboot": None,
                }
            )
    
        next_schedule_device_id = f"{self.controller_mac_address}_next_schedule"
        data.append(
            {
                "device_id": next_schedule_device_id,
                "device_type": "NEXT_SCHEDULE_SENSOR",
                "device_name": "Next schedule",
                "device_uid": _stable_uid(next_schedule_device_id),
                "software_version": "1.0",
                "state": self.next_schedule,
                "icon": "mdi:home-clock",
                "last_reboot": None,
            }
        )
    
        last_sprinkle_device_id = f"{self.controller_mac_address}_last_sprinkle"
        data.append(
            {
                "device_id": last_sprinkle_device_id,
                "device_type": "LAST_SPRINKLE_SENSOR",
                "device_name": "Last sprinkle",
                "device_uid": _stable_uid(last_sprinkle_device_id),
                "software_version": "1.0",
                "state": self.last_sprinkle,
                "icon": "mdi:sprinkler",
                "last_reboot": None,
            }
        )
    
        # Rain-related devices are only created when Weather is enabled
        if self.weather_api:
            last_rain_device_id = f"{self.controller_mac_address}_last_rain"
            data.append(
                {
                    "device_id": last_rain_device_id,
                    "device_type": "LAST_RAIN_SENSOR",
                    "device_name": "Last rain",
                    "device_uid": _stable_uid(last_rain_device_id),
                    "software_version": "1.0",
                    "state": self.last_rain,
                    "icon": "mdi:weather-pouring",
                    "last_reboot": None,
                }
            )
    
            rain_time_device_id = f"{self.controller_mac_address}_rain_time_today"
            data.append(
                {
                    "device_id": rain_time_device_id,
                    "device_type": "RAIN_TIME_TODAY_SENSOR",
                    "device_name": "Rain time today",
                    "device_uid": _stable_uid(rain_time_device_id),
                    "software_version": "1.0",
                    "state": self.rain_time_today,
                    "icon": "mdi:weather-rainy",
                    "last_reboot": None,
                }
            )
    
        total_water_device_id = f"{self.controller_mac_address}_total_water_consumption"
        data.append(
            {
                "device_id": total_water_device_id,
                "device_type": "TOTAL_WATER_CONSUMPTION_SENSOR",
                "device_name": "Total water consumption",
                "device_uid": _stable_uid(total_water_device_id),
                "software_version": "1.0",
                "state": self.total_water_consumption,
                "icon": "mdi:water-pump",
                "last_reboot": None,
            }
        )
    
        # Rain-related devices are only created when Weather is enabled
        if self.weather_api:
            total_rain_device_id = f"{self.controller_mac_address}_total_amount_rain_today"
            data.append(
                {
                    "device_id": total_rain_device_id,
                    "device_type": "TOTAL_AMOUNT_RAIN_TODAY",
                    "device_name": "Total amount of rain today",
                    "device_uid": _stable_uid(total_rain_device_id),
                    "software_version": "1.0",
                    "state": self.rain_total_amount_today,
                    "icon": "mdi:weather-rainy",
                    "last_reboot": None,
                }
            )
    
            total_forecasted_rain_device_id = (
                f"{self.controller_mac_address}_total_forecasted_rain_today"
            )
            data.append(
                {
                    "device_id": total_forecasted_rain_device_id,
                    "device_type": "TOTAL_FORECASTED_RAIN_TODAY",
                    "device_name": "Total forecasted rain today",
                    "device_uid": _stable_uid(total_forecasted_rain_device_id),
                    "software_version": "1.0",
                    "state": self.rain_total_amount_forecasted_today,
                    "icon": "mdi:weather-rainy",
                    "last_reboot": None,
                }
            )
    
        # Save persistent data
        await self.save_persistent_data()
        _LOGGER.debug(f"{self.controller_mac_address} - Updated sensors.")
        return data



    async def async_update_data(self):
        data = []

        try:
            data = await self.async_update_all_sensors()
        except Exception as err:
            # This will show entities as unavailable by raising UpdateFailed exception
            _LOGGER.error(f"{self.controller_mac_address} - Error: {err}", exc_info=True)

        # What is returned here is stored in self.data by the DataUpdateCoordinator
        return data


    async def calculate_rain_amount(self) -> float:
        if "rain" not in self.is_raining_now_json:
            return 0.0  # No rain
    
        rain_data = self.is_raining_now_json["rain"]
        
        for key in rain_data:
            if key.endswith("h") and key[:-1].isdigit():
                hours = int(key[:-1])  # Extract the number of hours
                rain_amount = rain_data[key]  # Amount of water during this period
                minutes = hours * 60  # Convert from hours to minutes
                return (rain_amount / minutes) * (self.poll_interval / 60)  # Returns the amount of water for the polling frequency

        return 0.0  # In case there are no recognizable keys

    def calculate_forecasted_sprinkle_today(self, station_id: int) -> float:
        """Calculate the planned sprinkle amount for today for a station.
    
        This value is based only on the schedule, station area and water flow rate.
        It does not subtract applied water or expected rain.
        """
        target_mm = self.sprinkle_target_amount_today[station_id - 1]
        return round(max(0.0, target_mm), 2)
    
    
    def calculate_remaining_sprinkle_today(self, station_id: int) -> float:
        """Calculate the remaining sprinkle amount needed today for a station."""
        target_mm = self.sprinkle_target_amount_today[station_id - 1]
        applied_mm = self.sprinkle_total_amount_today[station_id - 1]
        expected_rain_mm = self.rain_total_amount_forecasted_today
    
        remaining_mm = max(0.0, target_mm - (applied_mm + expected_rain_mm))
        return round(remaining_mm, 2)

    async def start_irrigation(self, station: int, minutes: int | None = None):
        """Start irrigation on a station."""
        duration = int(minutes if minutes is not None else self.irrigation_manual_duration)
    
        _LOGGER.info(
            f"{self.controller_mac_address} - Going to start watering on station {station} "
            f"for {duration} minutes..."
        )
    
        self.irrigation_stop_event.clear()
    
        try:
            if self._is_switch_control_method():
                await self.api.turn_on_station_switch(station)
    
                now = dt_util.now()
                end_at = now + timedelta(minutes=duration)
    
                self.active_irrigation = {
                    "station": int(station),
                    "start_at": now.isoformat(),
                    "end_at": end_at.isoformat(),
                    "duration_minutes": int(duration),
                }
    
                await self.save_persistent_data()
    
                # Reflect the intended state immediately.
                self.stations[station - 1].state = "Sprinkling"
    
                # Best-effort sync with the real switch state.
                # If the RainBird switch entity has already updated, this confirms the state.
                # If it has not updated yet, the state-change listener will correct it later.
                await self._async_sync_station_states_from_switches()
    
                # Extra safety: schedule a stop in case the watering loop is interrupted.
                self.hass.async_create_task(
                    self._async_stop_irrigation_after_delay(
                        int(station),
                        int(duration) * 60,
                    )
                )
            else:
                await self.api.sprinkle_station(station, duration)
                self.stations[station - 1].state = "Sprinkling"
    
        except APIConnectionError:
            _LOGGER.error(f"{self.controller_mac_address} - Failed due to connection error.")
            return
    
        data = await self.async_update_all_sensors()
    
        if data is not None:
            self.async_set_updated_data(data)
        else:
            _LOGGER.warning(
                f"{self.controller_mac_address} - async_update_all_sensors() returned None, skipping update."
            )
    
        for _ in range(duration * 60):
            if self.irrigation_stop_event.is_set():
                _LOGGER.info(f"{self.controller_mac_address} - Irrigation cancelation triggered.")
                break
    
            await sleep(1)
    
            self.total_water_consumption += self.water_flow_rate[station - 1] / 60
    
            flow_rate = self.water_flow_rate[station - 1]
            area = self.station_areas[station - 1] or 1
            mm_per_minute = flow_rate / area
    
            self.sprinkle_total_amount_today[station - 1] += mm_per_minute / 60
    
        else:
            _LOGGER.info(f"{self.controller_mac_address} - Finished watering on station {station}.")
    
        if self._is_switch_control_method():
            try:
                await self.api.turn_off_station_switch(station)
            except Exception:  # pylint: disable=broad-except
                _LOGGER.warning(
                    "%s - Failed turning off station switch %s",
                    self.controller_mac_address,
                    station,
                    exc_info=True,
                )
    
            self.active_irrigation = None
            await self.save_persistent_data()
    
            # Best-effort sync after turning the switch off.
            # If the switch state has already changed to off, the station becomes Stopped.
            # If not, the listener will update it when the switch entity changes.
            await self._async_sync_station_states_from_switches()
    
            switch_entity_id = self._get_station_switch_entity_id(station)
            switch_state = self.hass.states.get(switch_entity_id) if switch_entity_id else None
    
            if switch_state is None or switch_state.state != STATE_ON:
                self.stations[station - 1].state = "Stopped"
    
        else:
            self.stations[station - 1].state = "Stopped"
    
        self.last_sprinkle = dt_util.now()
    
        data = await self.async_update_all_sensors()
    
        if data is not None:
            self.async_set_updated_data(data)
        else:
            _LOGGER.warning(
                f"{self.controller_mac_address} - async_update_all_sensors() returned None, skipping update."
            )


    async def stop_irrigation(self):
        _LOGGER.info(f"{self.controller_mac_address} - Stopping watering...")
        try:
            if self._is_switch_control_method():
                await self.api.turn_off_all_station_switches()
            else:
                await self.api.stop_sprinkle()
        except APIConnectionError as ex:
            _LOGGER.error(f"{self.controller_mac_address} - Failed due to connection error.")
            return
    
        # Trigger event to stop sprinkling task
        self.irrigation_stop_event.set()
    
        if self._is_switch_control_method():
            self.active_irrigation = None
            await self.save_persistent_data()
    
        for station_id in range(1, self.num_stations + 1):
            self.stations[station_id - 1].state = "Stopped"
    
        _LOGGER.info(f"{self.controller_mac_address} - Stopped watering.")
        data = await self.async_update_all_sensors()
        self.async_set_updated_data(data)

    
    async def turn_controller_on(self):
        _LOGGER.info(f"{self.controller_mac_address} - Turning irrigation controller on...")

        try:
            await self.api.turn_on()
        except APIConnectionError as ex:
            _LOGGER.error(f"{self.controller_mac_address} - Failed due to connection error.")
            return
        
        self.controller.state = "On"
        
        data = await self.async_update_all_sensors()
        self.async_set_updated_data(data)
        _LOGGER.info(f"{self.controller_mac_address} - Irrigation controller turned on.")
    
    async def turn_controller_off(self):
        _LOGGER.info(f"{self.controller_mac_address} - Turning irrigation controller off..")
        try:
            await self.api.turn_off()
        except APIConnectionError as ex:
            _LOGGER.error(f"{self.controller_mac_address} - Failed due to connection error.")
            return

        self.controller.state = "Off"

        data = await self.async_update_all_sensors()
        self.async_set_updated_data(data)
        _LOGGER.info(f"{self.controller_mac_address} - Irrigation controller turned off.")


    async def async_set_schedule(self, new_schedule):
        """Replace irrigation schedule from the frontend card."""
        _LOGGER.info(f"{self.controller_mac_address} - Updating schedule.")
    
        self.schedule = new_schedule
    
        self.sprinkle_target_amount_today = await self.calculate_sprinkle_target_amounts(
            only_future_slots=True,
            include_already_applied=True,
        )
    
        self.forecasted_sprinkle_today = [
            self.calculate_forecasted_sprinkle_today(station_id)
            for station_id in range(1, self.num_stations + 1)
        ]
    
        self.remaining_sprinkle_today = [
            self.calculate_remaining_sprinkle_today(station_id)
            for station_id in range(1, self.num_stations + 1)
        ]
    
        await self.save_persistent_data()
    
        await self.check_and_schedule_watering()
    
        data = await self.async_update_all_sensors()
    
        if data is not None:
            self.async_set_updated_data(data)
        else:
            _LOGGER.warning(
                f"{self.controller_mac_address} - async_update_all_sensors() returned None after schedule update."
            )
    
        _LOGGER.info(f"{self.controller_mac_address} - Updated schedule.")

    
    async def initialize_schedule(self):
        """Initialize the schedule if not already set"""
        _LOGGER.info(f"{self.controller_mac_address} - Initializing schedule...")
    
        # Is there a storaged schedule
        if not self.schedule:
            _LOGGER.debug(f"{self.controller_mac_address} - No schedule found, creating a new one...")
    
            # Creates new schedule based on the number of stations
            new_schedule = [
                {
                    "interval_days": 0,
                    "stations": {
                        f"station_{i+1}_minutes": 0
                        for i in range(self.num_stations)
                    },
                    "hours": []
                }
                for _ in range(12)  # Lista com 12 meses
            ]
    
            self.schedule = new_schedule
    
            # Saves new schedule on storage
            await self.save_persistent_data()

            return
    
        # Verifies if the number of stations have changed and adapts schedule accordingly
        current_num_stations = len(next(iter(self.schedule))["stations"])

        if current_num_stations != self.num_stations:
            _LOGGER.debug(f"{self.controller_mac_address} - Updating schedule due to station count change.")
            for month_config in self.schedule:
                current_stations = month_config.get("stations", {})
                new_station_keys = {f"station_{i+1}_minutes" for i in range(self.num_stations)}
    
                # Adds new stations
                for new_station in new_station_keys - set(current_stations.keys()):
                    month_config["stations"][new_station] = 0
    
                # Remove obsolet stations
                for old_station in set(current_stations.keys()) - new_station_keys:
                    del month_config["stations"][old_station]
    
            # Saves the schedule on storage
            await self.save_persistent_data()

        _LOGGER.info(f"{self.controller_mac_address} - Schedule initialized.")

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
