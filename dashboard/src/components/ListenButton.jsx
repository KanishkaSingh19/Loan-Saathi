import { useEffect, useRef, useState } from "react";
import { speak, Unauthorized } from "../api";

// "Listen to this result": reads a short, deterministic summary (built on the server) with Gnani
// Timbre through /v1/tts. Tap again to stop.
export default function ListenButton({ apiKey, text, onUnauthorized = undefined }) {
  const [state, setState] = useState("idle"); // idle | loading | playing | error
  const audio = useRef(null);

  useEffect(
    () => () => {
      audio.current?.pause();
    },
    [],
  );

  if (!text || !apiKey) return null;

  const toggle = async () => {
    if (state === "playing" || state === "loading") {
      audio.current?.pause();
      setState("idle");
      return;
    }
    setState("loading");
    try {
      const blob = await speak(apiKey, text);
      const url = URL.createObjectURL(blob);
      audio.current?.pause();
      audio.current = new Audio(url);
      audio.current.onended = () => {
        URL.revokeObjectURL(url);
        setState("idle");
      };
      await audio.current.play();
      setState("playing");
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized?.();
      setState("error");
    }
  };

  return (
    <span className="listen">
      <button type="button" className="btn listen-btn" onClick={toggle} aria-pressed={state === "playing"}>
        <span aria-hidden="true">{state === "playing" ? "■" : "🔊"}</span>
        {state === "loading" ? " Preparing audio…" : state === "playing" ? " Stop" : " Listen to this result"}
      </button>
      {state === "error" && (
        <small className="muted" role="status">
          Voice playback isn&rsquo;t available right now. The result is written above.
        </small>
      )}
    </span>
  );
}
