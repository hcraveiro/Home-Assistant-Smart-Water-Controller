"""Constants for our integration."""

DOMAIN = "smart_water_controller"

DEFAULT_SCAN_INTERVAL = 60
MIN_SCAN_INTERVAL = 10
CONTROLLER_MAC_ADDRESS = "controller_mac_address"
NUM_STATIONS = "num_stations"
SPRINKLE_WITH_RAIN = "sprinkle_with_rain"
WEATHER_API_KEY = "weather_api_key"
SOIL_MOISTURE_SENSOR = "soil_moisture_sensor"
SOIL_MOISTURE_THRESHOLD = "soil_moisture_threshold"
DEFAULT_SOIL_MOISTURE = 40
MAX_SPRINKLES_PER_DAY = 5
MONTHS = [
    "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"
]

CHARACTERISTIC_UUID = "108b0002-eab5-bc09-d0ea-0b8f467ce8ee"
BLUETOOTH_TIMEOUT = "bluetooth_timeout"
BLUETOOTH_MIN_TIMEOUT = 5
BLUETOOTH_DEFAULT_TIMEOUT = 15

OPEN_WEATHER_MAP_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast?units=metric&"
OPEN_WEATHER_MAP_CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather?"
PIRATE_WEATHER_URL = "https://api.pirateweather.net/forecast/"
WEATHER_API_CACHE_TIMEOUT = "weather_api_cache_timeout"
WEATHER_API_CACHE_MIN_TIMEOUT = 1
WEATHER_API_CACHE_DEFAULT_TIMEOUT = 5

# Timeout (in seconds) for a single HTTP request to a weather provider. Without
# it a hanging provider would stall the coordinator poll indefinitely.
WEATHER_REQUEST_TIMEOUT = 30

# Rain accumulated today (mm) before rain counts as a real watering event that
# can postpone the next days through the schedule "interval_days" rule. Without
# a threshold a few minutes of drizzle suppressed watering for days.
MIN_SIGNIFICANT_RAIN_MM = 1.0

# Cap applied to the elapsed time used to integrate the current rain rate,
# expressed as a multiple of the poll interval. Protects the daily rain total
# from long gaps (restart, suspended host) counting as continuous rain.
MAX_RAIN_SAMPLE_INTERVAL_FACTOR = 2

# -----------------------------------------------------------------------------
# Irrigation control method
# -----------------------------------------------------------------------------
IRRIGATION_CONTROL_METHOD = "irrigation_control_method"
IRRIGATION_CONTROL_METHOD_SERVICE = "service"
IRRIGATION_CONTROL_METHOD_SWITCH = "switch"
IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT = "solem_toolkit"
IRRIGATION_CONTROL_METHOD_SOLEM_BL_NR = "solem_toolkit_bl_nr"
SOLEM_CONTROL_METHODS = (IRRIGATION_CONTROL_METHOD_SOLEM_TOOLKIT, IRRIGATION_CONTROL_METHOD_SOLEM_BL_NR)

# ----------------------------------------------------------------------------
# Switch-based control configuration
# ----------------------------------------------------------------------------

# A list with one switch entity_id per station, in station order (index 0 == station 1).
STATION_SWITCH_ENTITIES = "station_switch_entities"

# Weather provider selector (UI-facing)
WEATHER_PROVIDER = "weather_provider"
WEATHER_PROVIDER_NONE = "none"
WEATHER_PROVIDER_OPENWEATHERMAP = "openweathermap"
WEATHER_PROVIDER_PIRATEWEATHER = "pirateweather"

USE_SOIL_MOISTURE = "use_soil_moisture"

# -----------------------------------------------------------------------------
# Service-based control configuration (generic)
# -----------------------------------------------------------------------------

# The logical actions supported by this integration.
ACTION_SPRINKLE_STATION = "sprinkle_station"
ACTION_STOP_SPRINKLE = "stop_sprinkle"
ACTION_TURN_ON = "turn_on"
ACTION_TURN_OFF = "turn_off"

SUPPORTED_ACTIONS_IN_ORDER = [
    ACTION_SPRINKLE_STATION,
    ACTION_STOP_SPRINKLE,
    ACTION_TURN_ON,
    ACTION_TURN_OFF,
]

# Root config key holding the service mapping configuration.
SERVICE_ACTIONS = "service_actions"

# Per-action keys
SERVICE_ACTION_ENABLED = "enabled"
SERVICE_ACTION_SERVICE = "service"
SERVICE_ACTION_PARAMS = "params"

# Param keys
SERVICE_PARAM_NAME = "name"
SERVICE_PARAM_LABEL = "label"
SERVICE_PARAM_VALUE = "value"
SERVICE_PARAM_TYPE = "type"

# Param types
SERVICE_PARAM_TYPE_TIME = "time"
SERVICE_PARAM_TYPE_MAC = "mac_address"
SERVICE_PARAM_TYPE_STATION = "station"
SERVICE_PARAM_TYPE_OTHER = "other"

SUPPORTED_PARAM_TYPES = [
    SERVICE_PARAM_TYPE_TIME,
    SERVICE_PARAM_TYPE_MAC,
    SERVICE_PARAM_TYPE_STATION,
    SERVICE_PARAM_TYPE_OTHER,
]

# Solem Toolkit default service calls
SOLEM_TOOLKIT_SERVICE_SPRINKLE = "solem_toolkit.sprinkle_station_x_for_y_minutes"
SOLEM_TOOLKIT_SERVICE_STOP = "solem_toolkit.stop_manual_sprinkle"
SOLEM_TOOLKIT_SERVICE_TURN_ON = "solem_toolkit.turn_on"
SOLEM_TOOLKIT_SERVICE_TURN_OFF = "solem_toolkit.turn_off_permanent"


# Optional notification settings. The master switch is disabled on new installs.
NOTIFICATIONS_ENABLED = "notifications_enabled"
NOTIFY_STATION_FINISHED = "notify_station_finished"
NOTIFY_CYCLE_FINISHED = "notify_cycle_finished"
NOTIFY_IRRIGATION_FAILED = "notify_irrigation_failed"
NOTIFICATION_TARGETS = "notification_targets"

NOTIFICATION_DEFAULTS = {
    NOTIFICATIONS_ENABLED: False,
    NOTIFY_STATION_FINISHED: True,
    NOTIFY_CYCLE_FINISHED: True,
    NOTIFY_IRRIGATION_FAILED: True,
    NOTIFICATION_TARGETS: [],
}
