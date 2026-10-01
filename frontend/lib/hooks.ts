"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

export function useApi<T = any>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!path);

  const latest = useRef(0);
  const load = useCallback(async () => {
    if (!path) return;
    const mine = ++latest.current;  // only the most recent request may update state (responses can arrive out of order)
    try {
      setError(null);
      const res = await api<T>(path);
      if (mine === latest.current) setData(res);
    } catch (e: any) {
      if (mine === latest.current) setError(e.message ?? "Failed to load");
    } finally {
      if (mine === latest.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    setLoading(!!path);
    load();
  }, [load, path]);

  return { data, error, loading, refetch: load };
}
