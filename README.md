# Home Assistant Smart Water Controller Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/hcraveiro/Home-Assistant-Smart-Water-Controller.svg)](https://github.com/hcraveiro/Home-Assistant-Smart-Water-Controller/releases/)

Integrate irrigation controllers into Home Assistant with support for manual watering, scheduled watering, station-level planning, rain-aware watering and optional soil moisture protection.

This integration was originally created for **Solem Bluetooth Watering Controllers** and has been tested with **Solem BL-IP**, but it can also control other irrigation systems, such as **Rain Bird**, when each station is exposed as a Home Assistant switch.

The integration supports different irrigation control methods:

- **Station switches**: one Home Assistant switch per irrigation station.
- **Home Assistant services**: generic service-based control.
- **Solem Toolkit**: recommended when using compatible Solem Bluetooth controllers.

When configured with a weather provider, the integration can reduce or skip watering based on actual rain and forecasted rain.

---

## Table of Contents

- [Home Assistant Smart Water Controller Integration](#home-assistant-smart-water-controller-integration)
  - [Features](#features)
  - [Installation](#installation)
  - [Dependencies](#dependencies)
  - [Configuration](#configuration)
  - [Irrigation Control Methods](#irrigation-control-methods)
    - [Station switch control](#station-switch-control)
    - [Service-based control](#service-based-control)
    - [Solem Toolkit control](#solem-toolkit-control)
  - [Schedule Configuration](#schedule-configuration)
  - [Weather Providers](#weather-providers)
  - [Rain-Aware Watering Logic](#rain-aware-watering-logic)
  - [Soil Moisture Protection](#soil-moisture-protection)
  - [Sensors](#sensors)
    - [Controller and Station Sensors](#controller-and-station-sensors)
    - [Rain and Forecast Sensors](#rain-and-forecast-sensors)
    - [Irrigation Planning Sensors](#irrigation-planning-sensors)
    - [Water Usage Sensors](#water-usage-sensors)
    - [Configuration Entities](#configuration-entities)
    - [Actions and Controls](#actions-and-controls)
  - [How Watering Amounts Are Calculated](#how-watering-amounts-are-calculated)
  - [Slot-Based Watering Logic](#slot-based-watering-logic)
  - [Schedule Changes During the Day](#schedule-changes-during-the-day)
  - [Switch State Tracking](#switch-state-tracking)
  - [FAQ](#faq)

---

## Features

- Manual irrigation control per station.
- Monthly irrigation schedules.
- Multiple watering times per day.
- Per-station watering duration.
- Per-station lawn area.
- Per-station water flow rate.
- Automatic conversion from watering duration to applied water in millimeters.
- Rain-aware watering using actual rain and weather forecast data.
- Optional soil moisture sensor protection.
- Switch-based control for systems where each station is exposed as a Home Assistant switch.
- Automatic station status tracking from switch states.
- Persistent daily irrigation and rain counters.
- Automatic daily reset.
- HACS-compatible installation.

---

## Installation

This integration can be installed through HACS as a custom repository.

1. Open **HACS**.
2. Go to **Integrations**.
3. Open the menu in the top-right corner.
4. Select **Custom repositories**.
5. Add this repository URL:

   ```text
   https://github.com/hcraveiro/Home-Assistant-Smart-Water-Controller
   ```

6. Select category **Integration**.
7. Install **Smart Water Controller**.
8. Restart Home Assistant.
9. Add the integration from:

   ```text
   Settings → Devices & Services → Add Integration → Smart Water Controller
   ```

> To configure and visualize the irrigation schedule, install the companion card:
>
> [Smart Water Controller Schedule Card](https://github.com/hcraveiro/smart-water-controller-schedule-card)

---

## Dependencies

### Solem Toolkit

If you are using a compatible Solem Bluetooth controller, this integration uses:

[Home Assistant Solem Toolkit](https://github.com/hcraveiro/Home-Assistant-Solem-Toolkit)

Solem Toolkit is used to execute Solem-specific operations such as:

- Start sprinkling a station.
- Stop sprinkling.
- Turn the controller on.
- Turn the controller off.

If you are using the **station switch control** method, for example with Rain Bird station switches, Solem Toolkit may not be required.

---

## Configuration

For each irrigation controller, create one config entry.

During the configuration flow, you will be asked for:

- **Name**.
- **Irrigation control method**.
- **Number of stations**.
- **Station names**.
- **Station lawn areas**.
- **Controller location** using a Home Assistant `zone`.
- **Weather provider settings**.
- **Soil moisture settings**, if required.

The station area is important because it is used to convert watering duration and flow rate into millimeters of water applied.

---

## Irrigation Control Methods

### Station switch control

Use this method when each irrigation station is exposed as a Home Assistant switch.

Example:

```text
switch.rain_bird_sprinkler_1
switch.rain_bird_sprinkler_2
```

In this mode:

- Turning a station switch on starts watering that station.
- Turning a station switch off stops watering that station.
- The integration listens to switch state changes.
- If a station switch is turned on manually, the station status changes to `Sprinkling`.
- If a station switch is turned off manually or by the external controller, the station status changes to `Stopped`.
- If Home Assistant restarts while a station switch is already on, the integration syncs the station status from the switch state after startup.

This is useful for controllers such as Rain Bird when each zone or station is available as a switch.

### Service-based control

Use this method when irrigation actions are exposed through Home Assistant services.

The integration can call configured Home Assistant services to:

- Start watering a station.
- Stop watering.
- Turn the controller on.
- Turn the controller off.

### Solem Toolkit control

Use this method when using a compatible Solem Bluetooth controller.

This method delegates irrigation commands to Solem Toolkit.

---

## Schedule Configuration

Schedules are managed using the companion Lovelace card:

[Smart Water Controller Schedule Card](https://github.com/hcraveiro/smart-water-controller-schedule-card)

The schedule is configured per month.

Each month can define:

- Interval between watering days.
- Watering times.
- Watering duration per station.

Example schedule for one month:

```yaml
interval_days: 1
hours:
  - "08:00"
  - "20:00"
stations:
  station_1_minutes: 8
  station_2_minutes: 4
```

This means:

- Water every day.
- Run irrigation at 08:00 and 20:00.
- Station 1 has 8 minutes planned per watering slot.
- Station 2 has 4 minutes planned per watering slot.

---

## Weather Providers

Weather integration is optional.

If no weather provider is configured, the controller works normally without rain-aware adjustments.

Supported weather providers may include:

- `none`
- `openweathermap`
- `pirateweather`

Weather provider configuration may require:

- Provider selection.
- API key.
- Controller location.
- Cache timeout.
- Sprinkle with rain setting.

The integration uses weather data to calculate:

- Whether it is raining now.
- Whether rain is expected today.
- How much rain has already fallen today.
- How much rain is expected for the full day.

---

## Rain-Aware Watering Logic

The integration does not block the full day just because rain is forecasted.

The boolean sensor **Will it rain today** is informational. It does not directly prevent watering.

Instead, watering is adjusted using millimeters.

The important value is:

```text
Total forecasted rain today
```

This value represents:

```text
rain already detected today + rain still forecasted for today
```

The integration uses this value to reduce the amount of irrigation still needed.

The only rain-related condition that can directly stop watering at a scheduled slot is:

```text
Is it raining now
```

If it is currently raining and **sprinkle with rain** is disabled, watering is skipped.

---

## Soil Moisture Protection

An optional soil moisture sensor can be configured.

If a soil moisture sensor is configured and its value is equal to or above the configured threshold, watering is skipped.

This check happens at watering time.

Example:

```text
Soil moisture threshold: 60%
Current soil moisture: 65%
Result: watering skipped
```

---

## Sensors

This integration exposes several sensors per controller/config entry.

### Controller and Station Sensors

- **Controller status**

  Shows the controller state.

  The controller status entity also exposes useful attributes, including:

  - Schedule.
  - Number of stations.
  - Service prefix.
  - Controller service prefix.

- **Station status**

  One status sensor per station.

  Possible states include:

  - `Stopped`
  - `Sprinkling`

When using switch-based control, station status is synchronized from the configured station switches.

---

### Rain and Forecast Sensors

These sensors are only available when a weather provider is configured.

- **Is it raining now**

  Indicates whether it is currently raining.

- **Will it rain today**

  Indicates whether rain is forecasted today.

  This is informational only and does not directly block watering.

- **Has rained today**

  Indicates whether rain has been detected today.

- **Last rain**

  Timestamp of the last detected rain.

- **Rain time today**

  Total detected rain duration today, in minutes.

- **Total amount of rain today**

  Amount of rain already detected today, in millimeters.

- **Total forecasted rain today**

  Total rain expected for the day, in millimeters.

  This includes:

  ```text
  rain already detected today + rain still forecasted for today
  ```

---

### Irrigation Planning Sensors

These sensors are exposed per station.

- **Forecasted Sprinkle Today**

  Total planned irrigation for today, in millimeters.

  This is calculated from:

  ```text
  scheduled watering duration × water flow rate ÷ station area
  ```

  It does not subtract rain and does not subtract already applied irrigation.

- **Sprinkle Total Amount Today**

  Amount of water already applied today by irrigation, in millimeters.

- **Remaining Sprinkle Today**

  Amount of irrigation still needed today, in millimeters.

  This value considers:

  - Total planned irrigation for today.
  - Water already applied by irrigation.
  - Rain already detected today.
  - Rain still forecasted today.

  Formula:

  ```text
  Remaining Sprinkle Today =
      Forecasted Sprinkle Today
      - Sprinkle Total Amount Today
      - Total Forecasted Rain Today
  ```

  The result is never negative.

---

### Water Usage Sensors

- **Water flow rate**

  One configurable number entity per station.

  Unit:

  ```text
  L/min
  ```

  Used to convert irrigation duration into millimeters of water applied.

- **Total water consumption**

  Estimated total water consumption, in liters.

---

### Configuration Entities

- **Irrigation manual duration**

  Duration used when manually starting irrigation from the integration controls.

---

### Actions and Controls

Depending on the configured control method, the integration exposes controls such as:

- Sprinkle station.
- Stop sprinkle.
- Turn on controller.
- Turn off controller.

---

## How Watering Amounts Are Calculated

For each station, the integration converts minutes of irrigation into millimeters using:

```text
millimeters per minute = water flow rate / station area
```

Example:

```text
Water flow rate: 21 L/min
Station area: 100 m²

21 / 100 = 0.21 mm/min
```

If the station is scheduled to water for 8 minutes:

```text
8 × 0.21 = 1.68 mm
```

If the schedule has two watering slots in the day:

```text
1.68 × 2 = 3.36 mm planned for the day
```

The integration then adjusts the real watering duration based on:

- Water already applied today.
- Rain already detected today.
- Rain still forecasted today.
- Current scheduled slot.

---

## Slot-Based Watering Logic

When there are multiple watering times in a day, the integration uses slot-based accumulated targets.

Example:

```text
Daily target: 8 mm
Watering slots: 2
Target per slot: 4 mm
```

At the first slot, the accumulated target is:

```text
4 mm
```

At the second slot, the accumulated target is:

```text
8 mm
```

The integration compares the accumulated target with:

```text
already sprinkled today + total forecasted rain today
```

Example:

```text
Daily target: 8 mm
Watering slots: 2
Forecasted rain today: 3 mm
Already sprinkled: 0 mm
Current slot: 1

Accumulated target for this slot: 4 mm
Water needed now: 4 - 0 - 3 = 1 mm
```

At the second slot:

```text
Daily target: 8 mm
Forecasted rain today: 3 mm
Already sprinkled: 1 mm
Current slot: 2

Accumulated target for this slot: 8 mm
Water needed now: 8 - 1 - 3 = 4 mm
```

If rain has already fallen and more rain is still forecasted:

```text
Daily target: 8 mm
Already sprinkled: 1 mm
Rain already detected: 4 mm
Rain still forecasted: 2 mm
Total forecasted rain today: 6 mm
Current slot: 2

Water needed now: 8 - 1 - 6 = 1 mm
```

---

## Schedule Changes During the Day

If the schedule is changed during the day, the integration recalculates the remaining plan for today.

Only future watering slots are considered for the new schedule.

Water already applied today is preserved.

Example:

```text
Current time: 09:15
Already watered at: 08:00
New schedule:
  - 09:20
  - 20:00
```

The integration schedules the future slots:

```text
09:20
20:00
```

If instead the new schedule is:

```text
08:00
20:00
```

Then the 08:00 slot is ignored because it has already passed, and only 20:00 is scheduled.

---

## Switch State Tracking

When using station switch control, the integration tracks configured station switch entities.

Example:

```text
Station 1 switch: switch.rain_bird_sprinkler_1
Station 2 switch: switch.rain_bird_sprinkler_2
```

If a switch turns on:

```text
Station status = Sprinkling
```

If a switch turns off:

```text
Station status = Stopped
```

This also helps after Home Assistant restarts.

If Home Assistant restarts while a station switch is still on, the integration reads the switch state during startup and updates the station status accordingly.

If the external irrigation controller automatically turns off a station after its own maximum runtime, the switch state change is detected and the station status is updated.

---

## FAQ

### Can I use this integration with Rain Bird?

Yes, if each Rain Bird station is exposed as a Home Assistant switch.

Configure the integration using the **station switch control** method and select one switch per station.

### Can I use this integration with Solem controllers?

Yes.

The integration was originally built for Solem Bluetooth controllers and has been tested with **Solem BL-IP**.

For Solem controllers, use the Solem Toolkit control method.

### Does forecasted rain block the full day?

No.

Forecasted rain does not block the full day.

The integration subtracts the expected rain amount from the irrigation still needed.

### Does "Will it rain today" stop watering?

No.

**Will it rain today** is informational.

Watering decisions are based on millimeters, mainly:

```text
Total forecasted rain today
```

and:

```text
Remaining Sprinkle Today
```

### What happens if it is raining at the scheduled watering time?

If **sprinkle with rain** is disabled and **Is it raining now** is true, watering is skipped.

### What is the difference between Forecasted Sprinkle Today and Remaining Sprinkle Today?

**Forecasted Sprinkle Today** is the planned irrigation amount for the day, before subtracting rain or already applied irrigation.

**Remaining Sprinkle Today** is how much irrigation is still needed, after subtracting rain and already applied irrigation.

### Why did the history of Forecasted Sprinkle Today change?

Older versions of the integration used **Forecasted Sprinkle Today** to represent the amount still remaining.

Newer versions use:

```text
Forecasted Sprinkle Today = planned irrigation for today
Remaining Sprinkle Today = irrigation still needed today
```

Because the entity ID is preserved by Home Assistant, the historical graph may show old values with the previous meaning.

### Can I configure multiple watering times per day?

Yes.

Each month can have one or more watering times.

The integration adjusts each watering slot based on accumulated target, actual irrigation and expected rain.

### Can I configure different lawn sizes per station?

Yes.

Each station has its own lawn area.

The area is used together with water flow rate to calculate how many millimeters are applied per minute.

### Can I configure different water flow rates per station?

Yes.

Each station has a configurable water flow rate in liters per minute.

### Does the integration prevent overwatering?

The integration attempts to reduce overwatering by calculating the remaining amount still needed for the day.

It considers:

- Planned irrigation.
- Already applied irrigation.
- Rain already detected.
- Rain still forecasted.
- Soil moisture, if configured.

However, all calculations depend on the accuracy of the configured station area, water flow rate and weather provider data.

### Can I configure other controller models?

The integration can support other controllers if they can be controlled through Home Assistant services or station switches.

Direct controller-specific support depends on available APIs and testing.
