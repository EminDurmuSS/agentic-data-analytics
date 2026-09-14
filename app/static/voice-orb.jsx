import React from "react";
import { createRoot } from "react-dom/client";
import { Orb } from "orb-ui";

const mount = document.getElementById("voice-orb");
const root = mount ? createRoot(mount) : null;

// Bars colours use orb-ui's documented theme object; its renderer exposes
// only the size as a CSS custom property.
const theme = {
  name: "bars",
  preset: "calm",
  appearance: {
    colors: {
      idle: "#b8c9dc",
      connecting: "#58789b",
      listening: "#315f8e",
      thinking: "#274d79",
      speaking: "#002048",
      error: "#b35a18",
    },
    speakingGlow: 18,
  },
};

function FrequencyBars({ rootProps, controlProps, state, bands }) {
  const { className: rootClassName, style: rootStyle, ...rootAttributes } = rootProps;
  const { className: controlClassName, style: controlStyle, ...controlAttributes } = controlProps;
  const active = state === "speaking";
  return (
    <button
      {...rootAttributes}
      {...controlAttributes}
      className={[rootClassName, controlClassName, "voice-frequency-bars"].filter(Boolean).join(" ")}
      data-speaking={active}
      style={{ ...rootStyle, ...controlStyle, width: "min(100%, 360px)", height: "192px" }}
    >
      <span className="voice-frequency-bars__rail" aria-hidden="true">
        {bands.map((band, index) => (
          <i key={index} style={{ height: `${18 + Math.pow(band, 0.72) * 126}px` }} />
        ))}
      </span>
      {!active && <span className="voice-frequency-bars__label">Oynat</span>}
    </button>
  );
}

window.VoiceOrb = {
  render({ state, volume, bands, onStart, onStop }) {
    if (!root) return;
    root.render(
      <Orb
        aria-label="Ses özetini oynat veya duraklat"
        interactive
        onStart={onStart}
        onStop={onStop}
        state={state}
        volume={volume}
        theme={theme}
        renderTheme={(props) => <FrequencyBars {...props} bands={bands?.length ? bands : Array(17).fill(0)} />}
        size={360}
        style={{ "--orb-ui-size": "360px" }}
      />,
    );
  },
  clear() {
    root?.render(null);
  },
};
