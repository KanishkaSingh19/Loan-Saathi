import { useState } from "react";

export default function KeyGate({ retry, onSubmit }) {
  const [value, setValue] = useState("");
  return (
    <form
      className="keygate"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) onSubmit(value.trim());
      }}
    >
      <div className="brand-mark" aria-hidden="true">₹</div>
      <h2>Advisor sign-in</h2>
      <p className="muted">
        {retry
          ? "That key didn't work. Check WEBHOOK_KEY in your .env file, or in your host's environment settings."
          : "Pre-screens and advisor cases hold callers' personal data, so they need the staff key: WEBHOOK_KEY from your .env file (on Render, the service's Environment tab). It is kept in this browser tab only. Borrowers don't need it: Talk to Saathi works without signing in."}
      </p>
      <input
        type="password"
        autoComplete="off"
        placeholder="Webhook key"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        autoFocus
      />
      <button type="submit" className="btn primary" disabled={!value.trim()}>
        Sign in
      </button>
    </form>
  );
}
