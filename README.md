# Home Assistant Smart Water Controller Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/hcraveiro/Home-Assistant-Smart-Water-Controller.svg)](https://github.com/hcraveiro/Home-Assistant-Smart-Water-Controller/releases/)

Integrate **Solem Bluetooth Watering Controllers** (tested with **BL-IP**) into Home Assistant or other controllers (like Rainbird) that have switches for stations. This integration allows you to **manually control irrigation** and to **create and run irrigation schedules**, optionally using a **Weather Provider** to adapt watering based on rain and forecast.

- [Home Assistant Smart Water Controller Integration](#home-assistant-smart-water-controller-integration)
  - [Installation](#installation)
  - [Configuration](#configuration)
  - [Weather Providers](#weather-providers)
  - [Sensors](#sensors)
  - [FAQ](#faq)

---

## Installation

This integration can be added as a custom repository in HACS and installed from there.

### Dependencies

This integration depends on **[Solem Toolkit](https://github.com/hcraveiro/Home-Assistant-Solem-Toolkit)** to execute Solem operations (sprinkle a station for X minutes, stop sprinkling, turn controller on/off, etc.).

### Add Integration

After installing via HACS, add it in Home Assistant:

**Settings → Devices & Services → Add Integration → Smart Water Controller**

> If you want to configure and visualize the schedule, install the card
> **[Smart Water Controller Schedule Card](https://github.com/hcraveiro/smart-water-controller-schedule-card)**.

---

## Configuration

For each controller you want to use, you should create a **config entry**.

During the configuration flow you will be asked for:
- **Name** (optional)
- **Irrigation control method**:
  - Switch entities (one switch per station), or
  - Home Assistant service calls (generic), or
  - **Solem Toolkit** (recommended if using Solem)
- **Number of stations**
- **Station names and lawn areas** (area per station is used for irrigation calculations)
- **Controller location** (select one of your HA `zone` entities)
- **Weather provider settings** (optional)
- **Soil moisture settings** (optional)

### Schedule configuration

An irrigation schedule is managed via the UI card:
- **[Smart Water Controller Schedule Card](https://github.com/hcraveiro/smart-water-controller-schedule-card)**

The schedule configuration is intentionally not done in the config flow to keep the setup user-friendly.

---

## Weather Providers

Weather integration is **optional**. If you select `none`, the controller works normally without any weather/rain logic.

Supported providers depend on what is implemented in the integration, but typically include:
- `none`
- `pirateweather`
- (others may exist depending on your setup)

Weather provider configuration generally requires:
- Provider selection
- API key (if required by the provider)
- Cache timeout (options)
- “Sprinkle with rain” setting (true/false)

> The goal is that the **coordinator remains unchanged**: it always consumes the same weather/rain data structure from the API layer, regardless of provider. All provider-specific logic lives in `api.py` / `weather_providers/*`.

---

## Sensors

This integration exposes several sensors per controller/config entry:

### Controller & Stations
- **Controller status**: on/off (and attributes that include schedule information)
- **Station (n) status**: stopped / sprinkling

### Rain & Forecast
- **Has rained today**: true/false
- **Is it raining now**: true/false
- **Will it rain today**: true/false
- **Last rain**: datetime of the last time it rained
- **Rain time today**: total minutes of rain today
- **Total amount of rain today**: total mm of rain already detected today
- **Total forecasted rain today**: total mm expected today (taking into account what already rained + what is still forecasted)

### Irrigation Planning
- **Sprinkle total amount today (station n)**: total mm already applied today per station
- **Forecasted sprinkle today (station n)**: mm still planned to irrigate today per station (after considering rain + forecast)

### Water Usage
- **Water flow rate (station n)**: liters/minute configured per station
- **Total water consumption**: total liters consumed (based on sprinkle time and flow rate)

### Actions / Controls
Depending on the configured control method, the integration exposes actions such as:
- **Sprinkle station (n)**: start sprinkling station n for a given duration
- **Stop sprinkle**: stop any ongoing sprinkling
- **Turn on controller**
- **Turn off controller**
- **Irrigation manual duration**: configured duration for manual sprinkling (if applicable)

---

## FAQ

### Can I configure other controller models?

Not yet. This integration has only been tested with **BL-IP**. Support for other models may be added in the future once they are reverse engineered and tested.
