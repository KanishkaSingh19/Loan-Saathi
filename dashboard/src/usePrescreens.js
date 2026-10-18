import { useCallback, useEffect, useRef, useState } from "react";
import { fetchApplications, Unauthorized } from "./api";

const POLL_MS = 3000;

// Polls the webhook for pre-screens and calls. Reports "locked" on a bad key so the
// app can ask again, and stops polling until a new key arrives.
export function usePrescreens(key) {
  const [data, setData] = useState({ prescreens: [], calls: [] });
  const [status, setStatus] = useState(key ? "connecting" : "locked");
  const [updatedAt, setUpdatedAt] = useState(null);
  const keyRef = useRef(key);
  keyRef.current = key;

  const refresh = useCallback(async () => {
    if (!keyRef.current) return;
    try {
      const next = await fetchApplications(keyRef.current);
      setData(next);
      setStatus("live");
      setUpdatedAt(new Date());
    } catch (err) {
      setStatus(err instanceof Unauthorized ? "locked" : "offline");
    }
  }, []);

  useEffect(() => {
    if (!key) {
      setStatus("locked");
      return undefined;
    }
    setStatus("connecting");
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [key, refresh]);

  return { ...data, status, updatedAt, refresh };
}
