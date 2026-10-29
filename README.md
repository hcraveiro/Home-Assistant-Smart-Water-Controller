# Home Assistant Smart Water Controller Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/hcraveiro/Home-Assistant-Smart-Water-Controller.svg)](https://github.com/hcraveiro/Home-Assistant-Smart-Water-Controller/releases/)

Integrate irrigation controllers into Home Assistant with support for manual watering, scheduled watering, station-level planning, rain-aware watering, current irrigation tracking and optional soil moisture protection.

This integration was originally created for **Solem Bluetooth Watering Controllers** and supports **Solem BL-IP** and **Solem BL-NR**, but it can also control other irrigation systems, such as **Rain Bird**, when each station is exposed as a Home Assistant switch.

The integration supports different irrigation control methods:

- **Station switches**: one Home Assistant switch per irrigation station.
- **Home Assistant services**: generic service-based control.
- **Solem BL-IP / BL-NR presets**: preconfigured Solem Toolkit service mappings.

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
    - [Current Irrigation Sensors](#current-irrigation-sensors)
    - [Rain and Forecast Sensors](#rain-and-forecast-sensors)
    - [Irrigation Planning Sensors](#irrigation-planning-sensors)
    - [Water Usage Sensors](#water-usage-sensors)
    - [Configuration Entities](#configuration-entities)
    - [Actions and Controls](#actions-and-controls)
  - [How Watering Amounts Are Calculated](#how-watering-amounts-are-calculated)
  - [Slot-Based Watering Logic](#slot-based-watering-logic)
  - [Schedule Changes During the Day](#schedule-changes-during-the-day)
  - [Switch State Tracking](#switch-state-tracking)
  - [Active Irrigation Tracking and Restart Safety](#active-irrigation-tracking-and-restart-safety)
  - [Irrigation session safety](#irrigation-session-safety)
  - [Regression tests](#regression-tests)
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
- Current irrigation station tracking.
- Current irrigation end time tracking.
- Controller and station attributes for active irrigation metadata.
- Restart safety for active switch-based irrigation.
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
- Turning a station switch off requests shutdown. With Rain Bird, this stops the whole physical controller.
- The integration listens to switch state changes.
- If a station switch is turned on manually, the station status changes to `Sprinkling`.
- A confirmed shutdown is shown as `Stopped`. Failed or unconfirmed shutdowns remain pending; station status can be `Stopping` or `Unavailable`.
- If Home Assistant restarts during an integration-started switch session, shutdown is attempted immediately and retried when communication returns.

This is useful for controllers such as Rain Bird when each zone or station is available as a switch.

### Service-based control

Use this method when irrigation actions are exposed through Home Assistant services.

The action fields provide a searchable list of registered Home Assistant actions
with autocomplete; you can also enter `domain.service` manually. Saved actions
remain selectable even when temporarily unavailable.

Custom services only require the parameters their actions need. A MAC address is
not mandatory: for example, you can enable only Start and Stop and use entity IDs
or other service parameters. Solem BL-IP/BL-NR presets still require a MAC address
in at least one enabled action. This distinction applies to both setup and options.

The integration can call configured Home Assistant services to:

- Start watering a station.
- Stop watering.
- Turn the controller on.
- Turn the controller off.

### Solem Toolkit control

Use this method when using a compatible Solem Bluetooth controller.

This method delegates irrigation commands to Solem Toolkit.

Choose **Solem BL-IP (Solem Toolkit)** for BL-IP controllers, or
**Solem BL-NR (Solem Toolkit)** for a BL-NR controller.
The BL-NR profile requires Solem Toolkit **V1.0.5 or newer**.

#### BL-NR station count

The **BL-NR hardware has exactly one station**, as specified on the
[official SOLEM product page](https://solem-irrigation.com/produit/bl-nr/) and
[BL-NR technical sheet](https://solem-irrigation.com/wp-content/uploads/2023/12/BL-NR-EN.pdf).
Its preset therefore accepts exactly **one station** during setup,
reconfiguration and station-count options. This reflects the device's hardware,
not missing multi-station protocol support in Toolkit.

The `station` service parameter is still supplied dynamically from the active
station; for this profile its valid value is 1. BL-IP and Custom services keep
their configurable station count.

Both presets configure these actions:

| Smart Water Controller action | Solem Toolkit service |
| --- | --- |
| Start station | `solem_toolkit.sprinkle_station_x_for_y_minutes` |
| Stop | `solem_toolkit.stop_manual_sprinkle` |
| Enable controller | `solem_toolkit.turn_on` |
| Disable controller | `solem_toolkit.turn_off_permanent` |

Every new preset action explicitly sends `model: bl_ip` or `model: bl_nr`.
The action parameter editor uses an expandable list: use **+** to add a new
parameter and remove entries you do not need. Each item contains the parameter
name, description, value and value source. There is no fixed five-parameter limit.
Solem presets and existing mappings are prefilled, including the fixed model.
Enter the controller MAC in the action parameter forms. Station and watering
duration are filled from the active station and schedule at runtime, and the
Bluetooth timeout comes from the integration's Basic data options unless an
action explicitly overrides it. The BL-NR controller manages its own watering
timer; Smart Water Controller still manages scheduling, session tracking and Stop.

Existing BL-IP configurations retain their stored `solem_toolkit` method ID
and service mappings. A mapping without `model` continues to use Toolkit's
BL-IP default. Reconfiguring with the same preset preserves customized parameters;
selecting a different preset replaces the mappings with that preset's defaults.
Use **Reconfigure** to change between the presets, and **Options → Services** to
edit the action mappings. **Custom services** and **Station switches** remain
available for other controllers.


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
  - Irrigation source.
  - Current station.
  - Current station name.
  - Current irrigation duration.
  - Current irrigation start time.
  - Current irrigation end time.

- **Station status**

  One status sensor per station.

  Possible states include:

  - `Stopped`
  - `Sprinkling`
  - `Stopping` when shutdown is pending and the switch is reachable
  - `Unavailable` when the switch state is unknown, unavailable or missing

  Each station status entity also exposes irrigation attributes:

  - `irrigation_source`
  - `duration_minutes`
  - `started_at`
  - `end_at`
  - `stop_pending`

The controller status sensor also exposes `pending_stop_stations`.

When using switch-based control, station status is synchronized from the configured station switches.

---

### Current Irrigation Sensors

The integration exposes current irrigation sensors to make the active watering state easier to display in Lovelace.

- **Current Irrigation Station**

  Shows the station currently being watered.

  When no station is watering, this sensor shows:

  ```text
  Idle
  ```

- **Irrigation End Time**

  Timestamp of the expected end time for the current irrigation.

  This is especially useful in Home Assistant entity cards because timestamp sensors are displayed relatively by the frontend, for example:

  ```text
  in 7 minutes
  ```

  When no irrigation is active, this sensor is empty.

For irrigation started by the integration, the end time is known and persisted for restart safety.

For irrigation started externally by manually turning on a station switch, the integration can detect that the station is sprinkling, but it may not know the duration or end time.

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

If the schedule is changed during the day, the integration recalculates the full daily target from the active month's schedule. Water already applied today is preserved and subtracted from that target.

Only future callbacks are scheduled; past watering slots are not replayed immediately. Missed or interrupted watering remains in the daily amount still needed and can be made up at a later slot.

Saving the same schedule does not erase that deficit. For two daily five-minute slots at 21 L/min over 50 m², the target stays at 4.2 mm. If no water was applied in the morning, 4.2 mm remains; a subsequent 2.1 mm manual watering leaves 2.1 mm.

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

Only the 20:00 callback is scheduled because 08:00 has passed. Both slots still contribute to the daily target; the later slot accounts for the water already applied and any remaining deficit.

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

If a switch turns off during a session, the session stops accounting water and requests shutdown. `Stopped` is shown once shutdown is confirmed.

An unavailable, unknown or missing switch is shown as `Unavailable`. If an OFF command fails or is not confirmed, shutdown stays pending (`Stopping` when reachable), is persisted and is retried after reconnection.

This also helps after Home Assistant restarts.

If Home Assistant restarts while a station switch is still on, the integration reads the switch state during startup and updates the station status accordingly.

If the external irrigation controller automatically turns off a station after its own maximum runtime, the switch state change is detected and the station status is updated.

---

## Irrigation Outcomes and Optional Notifications (V1.1.6b5)

Irrigation failures are now visible without reading the Home Assistant log.

- A station that fails to start or unexpectedly stops can briefly show **Failed**
  (about 12 seconds), then return to **Stopped**. The controller's
  **Current Irrigation Station** becomes **Failed**, then **Idle**.
- The controller status sensor exposes `last_irrigation_result` and
  `last_irrigation_failure` attributes. Each station status sensor has a
  `last_irrigation_result` attribute. These results are persisted across
  Home Assistant restarts. A later successful watering does **not** erase
  the last known failure.
- Scheduled switch-based watering continues to retry once before a failure
  is reported. Intentional **Stop** and **Stop All** actions are not failures.
- A Bluetooth service acknowledgement confirms that the command was
  accepted, but without physical valve feedback the integration cannot
  guarantee that water flowed.

### Notifications (off by default)

The **final step** of initial setup and reconfiguration is **Notifications**.
You can also edit subscriptions at any time via **Settings → Devices &
Services → Smart Water Controller → Configure → Notifications**.

Enable the master notification switch, choose the events you want, and select
one or more mobile app notification services detected in Home Assistant.
A configuration without recipients is rejected when notifications are enabled.

| Event | Fixed notification message |
| --- | --- |
| Station watering completed | `Sprinkling finished on station Front Lawn 1.` |
| Manual Run Cycle completed | `Manual cycle finished on Rainbird.` |
| Scheduled cycle completed | `Scheduled cycle finished on Rainbird.` |
| Irrigation failed definitively | `Sprinkling failed on Front Lawn 1.` |
| Manual Stop on a station (individual watering or cycle) | `Sprinkling manually stopped on station Front Lawn 1.` |
| Stop All on a manual cycle | `Manual cycle stopped on Rainbird.` |
| Stop All on a scheduled cycle | `Scheduled cycle stopped on Rainbird.` |
| Stop All during standalone watering | `All irrigation manually stopped on Rainbird.` |

The four subscriptions can be switched on/off separately. They default to
enabled *within* the notifications setup, but the **master switch defaults to off**;
no messages are sent until notification recipients are selected and notifications
are explicitly enabled. Message wording is fixed and not editable.

Only a successfully finished station run raises *Station watering completed*.
Only cycles that ran and completed successfully raise *Cycle completed*.
A failure is reported after the scheduled recovery attempt is exhausted,
rather than on a transient retry. A confirmed intentional station Stop or
Stop All produces an independent **Irrigation manually stopped** notification
when enabled. It does not trigger success or failure notifications.
Duplicate Stop All requests without a new run are ignored for notifications.
If a station is manually skipped, the cycle will not send a misleading
*Cycle completed* notification.

Notification messages are maintained in `strings.json` and shipped in
`translations/en.json` under `common` translation keys, with runtime
placeholders for station and controller names. The integration reads the
Home Assistant translation catalog; text is fixed rather than editable.

## Active Irrigation Tracking and Restart Safety

When irrigation is started by the integration, the active irrigation metadata is persisted.

This includes:

- Station.
- Source.
- Start time.
- End time.
- Duration in minutes.

For integration-started switch sessions, a Home Assistant restart triggers immediate shutdown rather than resuming the interrupted session. If a switch is unavailable or an OFF command fails, the pending shutdown is preserved and retried when the switch becomes available, with periodic retries while unresolved.

The integration does not report a confirmed stop merely because an OFF command was attempted. Both a successful command and an `off` state are required. New sessions are blocked until all pending shutdowns are confirmed.


If a station switch is turned on externally, outside the integration, the integration detects the station as `Sprinkling`, but it may not know the irrigation duration or end time.

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

### How do I see how much time is left for the current irrigation?

Use the **Irrigation End Time** sensor.

Because this is a timestamp sensor, Home Assistant cards can display it relatively, for example:

```text
in 7 minutes
```

### What happens if Home Assistant restarts during irrigation?

For irrigation started by the integration using station switches, the interrupted session is stopped immediately when reachable. It is not resumed.

Failed or unconfirmed shutdowns survive restart and are retried when communication returns. New sessions remain blocked until shutdown is confirmed.

### Can the integration know the end time if I manually turn on the station switch?

Not always.

If the switch is turned on outside the integration, the integration can detect that the station is sprinkling, but it may not know the intended duration or end time.

In that case, the station status is updated, but **Irrigation End Time** may remain empty.

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


## Irrigation session safety

Only one session may control a controller at a time. A manual start replaces the
previous session after its shutdown has completed; it also abandons the rest of
an automatic sequence. An explicit stop abandons the sequence too. Every session
owns its worker and deadline, so a replaced session cannot later stop a new zone
or a new run of the same zone. Unloading stops the session and removes callbacks.

For switch control, all configured station switches must have a known `on` or
`off` state before a new run is allowed. An unavailable, unknown or missing switch
refuses the start and records an error with the station and entity ID. A start
must receive an `on` acknowledgement within 10 seconds. Rain Bird switches use
`rainbird.start_irrigation` with the requested duration rather than the switch's
configured default duration. Other switch integrations continue using
`switch.turn_on`.

If communication is lost during irrigation, the session stops accounting water
and records a persistent shutdown obligation. The same applies to failed or
unconfirmed start/stop commands. The integration sends OFF again as soon as the
switch becomes available, with a five-second retry while a shutdown is unresolved.
No new session is allowed until every pending stop has both a successful OFF
command and an `off` state. This serialization matters for Rain Bird, where
turning off any zone stops the whole physical controller.

Interrupted switch sessions found after a Home Assistant restart are stopped
immediately when reachable, rather than resumed. Pending stops survive restart.
Station statuses distinguish `Unavailable` and `Stopping` from confirmed
`Stopped`. The controller status sensor exposes `pending_stop_stations`; each
station status sensor exposes `stop_pending`.

Water consumption is an estimate from configured flow and elapsed, confirmed ON
time, not a physical meter. No water is added while the switch is unavailable or
a stop is pending. Time when the physical valve cannot be observed is unknown.
Automatic runs use a precise session deadline for the planned amount, even when
the hardware requires a duration rounded up to whole minutes.

The daily target is the full active day's schedule. Saving that schedule keeps
missed or interrupted watering in the remaining amount. For example, two daily
five-minute slots at 21 L/min over 50 m² give a 4.2 mm daily target. If the morning
slot is missed, saving the schedule still leaves 4.2 mm to apply; a subsequent
2.1 mm manual watering leaves 2.1 mm. Slot execution is capped by the same daily
target used by the sensors, and startup recalculates legacy cached targets.

Generic controllers without a device unique ID use an entry-specific storage key
so their schedules and pending shutdowns cannot overwrite one another. The legacy
shared `irrigation_None` storage is copied only when exactly one generic controller
exists and its new storage is absent. The original copy is preserved. With multiple
generic controllers, that legacy shared file cannot be safely attributed automatically.

### Regression tests

Run `python -m unittest discover -s tests -v` and
`python -m compileall -q custom_components`. The tests execute the production
runtime, planning, API and persistence modules with minimal Home Assistant
boundary fakes; they require no installed HA instance and never operate valves.
CI runs them on Python 3.12 and 3.13. Normal sequential watering was also verified
on a Rain Bird controller across evening and morning cycles: each zone completed,
the switches returned to OFF, and applied/remaining amounts matched the daily target.
The disconnect/reconnect and failed-command scenarios are covered by automated tests;
physical reconnection behavior still requires a separate field test.

