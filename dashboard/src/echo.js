// Pure helper for live voice (no browser APIs), so it runs under node --test.

/** True when a heard transcript is mostly Saathi's own reply coming back through the speakers. */
export function looksLikeEcho(heard, reply) {
  const words = (t) => (t || "").toLowerCase().normalize("NFKC").replace(/[^\p{L}\p{N}\s]/gu, " ").split(/\s+/).filter(Boolean);
  const h = words(heard);
  if (h.length < 2 || !reply) return false;
  const r = new Set(words(reply));
  return h.filter((w) => r.has(w)).length / h.length >= 0.7;
}
