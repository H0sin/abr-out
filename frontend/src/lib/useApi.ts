import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api";

export type Resource<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
  setData: (updater: (cur: T | null) => T | null) => void;
};

export function useResource<T>(fn: () => Promise<T>, deps: unknown[] = []): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const run = useCallback(async () => {
    setLoading(true);
    try {
      const out = await fnRef.current();
      setData(out);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "خطا در دریافت اطلاعات");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const update = useCallback(
    (updater: (cur: T | null) => T | null) => setData(updater),
    [],
  );

  return { data, loading, error, refetch: run, setData: update };
}
