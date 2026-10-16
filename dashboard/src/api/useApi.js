import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./client.js";

// Load JSON from the admin API. Re-fetches when `path` or `params` change.
// Returns { data, error, loading, reload }.
export function useApi(path, params) {
  const key = JSON.stringify([path, params || {}]);
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const seq = useRef(0);

  const load = useCallback(async () => {
    const mine = ++seq.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await api(path, { params });
      if (mine === seq.current) setState({ data, error: null, loading: false });
    } catch (error) {
      if (mine === seq.current) setState({ data: null, error, loading: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => {
    load();
  }, [load]);

  return { ...state, reload: load };
}
