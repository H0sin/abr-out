import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { ApiError, Me, api } from "../api";

type Ctx = {
  me: Me | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
};

const MeCtx = createContext<Ctx | null>(null);

export function MeProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.me();
      setMe(data);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "خطا در دریافت اطلاعات");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <MeCtx.Provider value={{ me, loading, error, refresh }}>
      {children}
    </MeCtx.Provider>
  );
}

export function useMe(): Ctx {
  const ctx = useContext(MeCtx);
  if (!ctx) throw new Error("useMe must be used inside MeProvider");
  return ctx;
}
