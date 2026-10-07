/**
 * VAV Balancer Lovelace card.
 *
 * A single self-contained custom element, no build step: Home Assistant's
 * frontend loads it directly as an ES module (see __init__.py, which
 * registers it via add_extra_js_url). Reads everything it shows from the
 * master fan.vav_balancer entity's attributes -- see DOCS.md "Master
 * entity" for the exact attribute shapes this relies on.
 */

const FLOW_ATTR_KEYS = [
  ["target_intake_m3h", "actual_intake_m3h", "Intake"],
  ["target_exhaust_m3h", "actual_exhaust_m3h", "Exhaust"],
];

function fmt(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Number(value).toFixed(digits);
}

function objectId(entityId) {
  return entityId.split(".").slice(1).join(".");
}

class VavBalancerCard extends HTMLElement {
  setConfig(config) {
    if (!config.entity) {
      throw new Error("vav-balancer-card: `entity` (the master fan entity) is required");
    }
    this._config = config;
    this._expanded = new Set();
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }
  }

  // Home Assistant calls this setter on every state change.
  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    const state = this._hass && this._hass.states[this._config.entity];
    const fanCount = state
      ? Object.keys(state.attributes.intake_targets || {}).length +
        Object.keys(state.attributes.exhaust_targets || {}).length
      : 2;
    return 3 + Math.ceil(fanCount / 2);
  }

  static getStubConfig(hass) {
    const guess = Object.keys(hass.states).find(
      (e) => e.startsWith("fan.") && e.includes("vav_balancer")
    );
    return { entity: guess || "fan.vav_balancer" };
  }

  _call(domain, service, data) {
    this._hass.callService(domain, service, data);
  }

  _findRelated(domainPrefix, suffix) {
    // The master entity's object_id is the device slug; related helper
    // entities share that same device, so look for one whose object_id
    // ends with the given suffix within the requested domain.
    if (!this._hass) return null;
    const masterObjectId = objectId(this._config.entity);
    const base = masterObjectId.replace(/^vav_balancer_?/, "") || "vav_balancer";
    return Object.keys(this._hass.states).find(
      (e) => e.startsWith(domainPrefix + ".") && e.includes(base) && e.includes(suffix)
    );
  }

  _boostEntity() {
    return this._config.boost_entity || this._findRelated("button", "boost");
  }

  _toggleExpanded(key) {
    if (this._expanded.has(key)) this._expanded.delete(key);
    else this._expanded.add(key);
    this._render();
  }

  _renderFanRow(entityId, target, role) {
    const key = `${role}:${entityId}`;
    const expanded = this._expanded.has(key);
    const flags = [];
    if (target.read_only) flags.push("read-only");
    if (target.paused) flags.push("paused");
    if (target.stuck_seconds) flags.push(`stuck ${fmt(target.stuck_seconds)}s`);
    if (!target.available) flags.push("unavailable");
    const rules = target.rules || [];

    const rulesHtml = rules.length
      ? `<table class="rules">
          <tr><th>sensor</th><th>mode</th><th>threshold</th><th>output</th><th>active</th></tr>
          ${rules
            .map(
              (r) => `<tr class="${r.active ? "rule-active" : ""}">
                <td>${r.sensor}</td><td>${r.mode}</td>
                <td>${fmt(r.threshold, 1)}${
                  r.threshold_source !== "fixed" ? ` (${r.threshold_source})` : ""
                }</td>
                <td>${r.output === null || r.output === undefined ? "—" : fmt(r.output, 1)}</td>
                <td>${r.active ? "✓" : "—"}</td>
              </tr>`
            )
            .join("")}
        </table>`
      : `<div class="no-rules">No rules configured.</div>`;

    return `
      <div class="fan-row ${expanded ? "expanded" : ""}" data-key="${key}">
        <div class="fan-row-main">
          <span class="fan-name">${objectId(entityId)}</span>
          <span class="fan-flags">${flags.map((f) => `<span class="flag">${f}</span>`).join("")}</span>
          <span class="fan-flow">${fmt(target.target_flow_m3h)} m³/h</span>
          <span class="fan-level">${target.control === "steps" ? "step " : ""}${fmt(
            target.target_level, target.control === "steps" ? 0 : 1
          )}${target.control === "percentage" ? "%" : ""}</span>
        </div>
        ${expanded ? `<div class="fan-row-detail">${rulesHtml}</div>` : ""}
      </div>`;
  }

  _render() {
    if (!this._hass || !this._config) return;
    const state = this._hass.states[this._config.entity];
    const root = this.shadowRoot;

    if (!state) {
      root.innerHTML = `<ha-card><div class="not-found">Entity ${this._config.entity} not found</div></ha-card>`;
      return;
    }

    const a = state.attributes;
    const title = this._config.title || a.friendly_name || "VAV Balancer";
    const on = state.state === "on";
    const boostEntity = this._boostEntity();
    const boostState = boostEntity ? this._hass.states[boostEntity] : null;
    const boosting = a.boost_active;

    const flowRows = FLOW_ATTR_KEYS.map(([targetKey, actualKey, label]) => {
      const target = a[targetKey];
      const actual = a[actualKey];
      const max = Math.max(target || 0, actual || 0, 1);
      return `
        <div class="flow-row">
          <div class="flow-label">${label}</div>
          <div class="flow-bars">
            <div class="bar target-bar" style="width:${(100 * (target || 0)) / max}%"></div>
            <div class="bar actual-bar" style="width:${(100 * (actual || 0)) / max}%"></div>
          </div>
          <div class="flow-numbers">
            <span class="target-num">target ${fmt(target)}</span>
            <span class="actual-num">actual ${fmt(actual)}</span>
          </div>
        </div>`;
    }).join("");

    const deltaClass =
      a.actual_delta_m3h < -(a.pressure_tolerance_m3h || 15) ? "delta-bad" : "delta-ok";

    const intakeEntries = Object.entries(a.intake_targets || {});
    const exhaustEntries = Object.entries(a.exhaust_targets || {});

    root.innerHTML = `
      <style>${VavBalancerCard._styles()}</style>
      <ha-card>
        <div class="header">
          <div class="title">${title}</div>
          <div class="header-actions">
            ${
              boosting
                ? `<button class="chip chip-boost-active" id="cancel-boost">
                     Boosting (${fmt((a.boost_remaining_seconds || 0) / 60, 1)} min left) — tap to cancel
                   </button>`
                : boostEntity
                ? `<button class="chip" id="start-boost">Boost</button>`
                : ""
            }
            <button class="chip ${on ? "chip-on" : "chip-off"}" id="toggle-master">
              ${on ? "ON" : "OFF"}
            </button>
          </div>
        </div>

        <div class="flows">${flowRows}</div>

        <div class="delta ${deltaClass}">
          Δ actual ${fmt(a.actual_delta_m3h)} m³/h &nbsp;·&nbsp; Δ target ${fmt(a.target_delta_m3h)} m³/h
        </div>

        ${
          intakeEntries.length
            ? `<div class="section-title">Intake</div>${intakeEntries
                .map(([id, t]) => this._renderFanRow(id, t, "intake"))
                .join("")}`
            : ""
        }
        ${
          exhaustEntries.length
            ? `<div class="section-title">Exhaust</div>${exhaustEntries
                .map(([id, t]) => this._renderFanRow(id, t, "exhaust"))
                .join("")}`
            : ""
        }
      </ha-card>
    `;

    const toggle = root.getElementById("toggle-master");
    if (toggle) {
      toggle.addEventListener("click", () =>
        this._call("fan", on ? "turn_off" : "turn_on", { entity_id: this._config.entity })
      );
    }
    const startBoost = root.getElementById("start-boost");
    if (startBoost) {
      startBoost.addEventListener("click", () =>
        this._call("button", "press", { entity_id: boostEntity })
      );
    }
    const cancelBoost = root.getElementById("cancel-boost");
    if (cancelBoost) {
      cancelBoost.addEventListener("click", () => this._call("vav_balancer", "cancel_boost", {}));
    }
    root.querySelectorAll(".fan-row").forEach((el) => {
      el.addEventListener("click", (ev) => {
        // Avoid double-toggling if a future version adds buttons inside a row.
        if (ev.target.closest("button")) return;
        this._toggleExpanded(el.dataset.key);
      });
    });
  }

  static _styles() {
    return `
      ha-card { padding: 16px; }
      .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
      .title { font-size: 1.2em; font-weight: 500; color: var(--primary-text-color); }
      .header-actions { display: flex; gap: 8px; }
      .chip {
        border: none; border-radius: 16px; padding: 6px 12px; font-size: 0.85em;
        cursor: pointer; color: var(--primary-text-color);
        background: var(--secondary-background-color, #eee);
      }
      .chip-on { background: var(--state-active-color, #43a047); color: white; }
      .chip-off { background: var(--secondary-background-color, #eee); }
      .chip-boost-active { background: var(--warning-color, #ff9800); color: white; }
      .flows { display: flex; flex-direction: column; gap: 10px; margin-bottom: 8px; }
      .flow-row { display: flex; flex-direction: column; gap: 2px; }
      .flow-label { font-size: 0.8em; color: var(--secondary-text-color); }
      .flow-bars {
        position: relative; height: 10px; background: var(--divider-color, #e0e0e0);
        border-radius: 5px; overflow: hidden;
      }
      .bar { position: absolute; top: 0; left: 0; height: 100%; border-radius: 5px; }
      .target-bar { background: var(--secondary-text-color, #888); opacity: 0.35; }
      .actual-bar { background: var(--primary-color, #03a9f4); opacity: 0.9; }
      .flow-numbers { display: flex; justify-content: space-between; font-size: 0.75em; color: var(--secondary-text-color); }
      .delta {
        text-align: center; padding: 6px; border-radius: 8px; font-size: 0.85em;
        margin-bottom: 12px;
      }
      .delta-ok { background: var(--secondary-background-color, #eee); color: var(--primary-text-color); }
      .delta-bad { background: var(--error-color, #db4437); color: white; }
      .section-title {
        font-size: 0.75em; text-transform: uppercase; letter-spacing: 0.05em;
        color: var(--secondary-text-color); margin: 10px 0 4px;
      }
      .fan-row {
        border-top: 1px solid var(--divider-color, #e0e0e0); padding: 6px 2px;
        cursor: pointer;
      }
      .fan-row-main { display: flex; align-items: center; gap: 8px; font-size: 0.9em; }
      .fan-name { flex: 1 1 auto; color: var(--primary-text-color); }
      .fan-flags { display: flex; gap: 4px; }
      .flag {
        font-size: 0.7em; padding: 1px 6px; border-radius: 8px;
        background: var(--secondary-background-color, #eee); color: var(--secondary-text-color);
      }
      .fan-flow { color: var(--secondary-text-color); min-width: 70px; text-align: right; }
      .fan-level { color: var(--secondary-text-color); min-width: 60px; text-align: right; }
      .fan-row-detail { padding: 6px 2px 2px; }
      .rules { width: 100%; border-collapse: collapse; font-size: 0.75em; }
      .rules th { text-align: left; color: var(--secondary-text-color); font-weight: 400; }
      .rules td, .rules th { padding: 2px 6px 2px 0; }
      .rule-active { color: var(--primary-color, #03a9f4); }
      .no-rules { font-size: 0.75em; color: var(--secondary-text-color); padding: 2px; }
      .not-found { padding: 16px; color: var(--error-color, #db4437); }
    `;
  }
}

customElements.define("vav-balancer-card", VavBalancerCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "vav-balancer-card",
  name: "VAV Balancer",
  description: "Target/actual intake and exhaust, pressure delta, and a per-fan breakdown with active rules for the VAV Ventilation Balancer integration.",
});
