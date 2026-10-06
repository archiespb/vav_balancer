# VAV Ventilation Balancer for Home Assistant

[Русская версия](README.ru.md)

<img src="https://raw.githubusercontent.com/archiespb/vav_balancer/main/custom_components/vav_balancer/brand/icon@2x.png" width="128" alt="VAV Balancer">

A generic, asynchronous intake/exhaust ventilation (VAV — Variable Air Volume) balancer with full step-by-step UI configuration. The integration isn't tied to specific rooms, fan models, or sensor types: intake and exhaust are assembled from whichever `fan` entities already exist in your Home Assistant, and their behaviour is defined entirely through the config wizard — no YAML required.

Highlights: any number of intake and exhaust fans (step- or percentage-controlled), sensor rules with dynamic thresholds (including another sensor's live value as the threshold), debounce and hysteresis against chattering at a threshold, day/night ceilings, a "read-only" mode for autonomous fans, continuous intake↔exhaust balance with protection against imbalance, configuration export/import, ready-made sensors for dashboard graphs, and one-click diagnostics.

Full details on every mechanism are in **[DOCS.md](DOCS.md)**.

Requires Home Assistant **2024.12** or newer.

## Installation

### Via HACS

This integration isn't in the HACS default store yet, so add it as a custom repository:

1. **HACS → Integrations → ⋮ (top right) → Custom repositories**.
2. Enter the repository URL (`https://github.com/archiespb/vav_balancer`), set **Category** to **Integration**, and click **Add**.
3. Find **VAV Ventilation Balancer** in HACS and click **Download**.
4. Restart Home Assistant.
5. **Settings → Devices & services → Add integration → VAV Ventilation Balancer**.

### Manual

1. Copy the `custom_components/vav_balancer` folder into your Home Assistant's `config/custom_components/` directory.
2. Restart Home Assistant.
3. **Settings → Devices & services → Add integration → VAV Ventilation Balancer**.

## Removal

1. **Settings → Devices & services → VAV Ventilation Balancer → ⋮ → Delete**. This stops both execution contours, unregisters every state tracker, and removes the config entry along with all of its entities and its single device; the fans and sensors you pointed it at are not affected.
2. To also remove the integration's code, stop Home Assistant after deleting the entry, delete the `config/custom_components/vav_balancer` folder, and restart again.

If you need the configuration to set up another instance, export it before deleting (**Configure → Export current configuration** — see [DOCS.md](DOCS.md#export-and-import-of-the-configuration)).

## Repository structure

| Path | Purpose |
|---|---|
| `custom_components/vav_balancer/manifest.json` | Integration manifest (domain, version, dependencies) |
| `custom_components/vav_balancer/const.py` | Config keys, defaults, limits |
| `custom_components/vav_balancer/models.py` | Fan models (`FanModel`) and sensor rules (`Rule`) |
| `custom_components/vav_balancer/balancer.py` | Pure balancing math (no Home Assistant I/O) |
| `custom_components/vav_balancer/controller.py` | Master controller: Contour 1 (calculation) and Contour 2 (execution) |
| `custom_components/vav_balancer/entity.py` | Shared base entity for every platform |
| `custom_components/vav_balancer/fan.py` | The single master `FanEntity` |
| `custom_components/vav_balancer/sensor.py` | Performance sensors for dashboard graphs |
| `custom_components/vav_balancer/binary_sensor.py` | "Night mode" / "home mode" sensors |
| `custom_components/vav_balancer/diagnostics.py` | Diagnostics export |
| `custom_components/vav_balancer/config_flow.py` | Config wizard, `OptionsFlowHandler`, export/import |
| `custom_components/vav_balancer/translations/` | UI strings (en, ru) |
| `tests/` | `pytest` suite (see [CONTRIBUTING.md](CONTRIBUTING.md)) |
| `dashboard_example.yaml` | Example Lovelace dashboard with graphs and rules |

## Quick setup overview

Both on first adding the integration and in **Configure** (`OptionsFlow`), a menu opens first: the step-by-step wizard, import, or (once already configured) export.

Step-by-step wizard: pick intake and exhaust fans → for each fan, its control type (steps/percentage), its airflow map, day/night ceilings, and sensor rules → global settings (presence minimums, quiet hours, command interval, pressure tolerance). The full description of every step, every rule field (dynamic threshold, debounce, hysteresis), and the export/import format is in [DOCS.md](DOCS.md#config-wizard--full-step-by-step-reference).

## Master entity and dashboard

The integration creates one master entity, `fan.vav_balancer` (turns actual hardware control on/off), plus a set of sensors (`sensor.vav_balancer_*`, `binary_sensor.vav_balancer_*`) for target/actual intake, exhaust, and pressure graphs — no third-party cards needed. The full entity and attribute reference, plus a ready-made dashboard example, is in [DOCS.md](DOCS.md#dashboard-entities).

## Logging and diagnostics

```yaml
logger:
  default: warning
  logs:
    custom_components.vav_balancer: debug
```

For a bug report: **Settings → Devices & services → VAV Ventilation Balancer → Download diagnostics**. Details in [DOCS.md](DOCS.md#logging-and-diagnostics).

## Limitations

- One config entry per installation.
- `airflow_map` values must be non-decreasing; the map is set by hand from the fan's datasheet or measurements.
- Presence minimums apply as a floor to intake and exhaust fans at the same time. If they exceed what the other side can supply, positive pressure still takes priority.

## Development and tests

See [CONTRIBUTING.md](CONTRIBUTING.md).
