import { Outlet } from "react-router-dom";
import { useMe } from "../../lib/MeContext";
import { EmptyState } from "../../components/ui";

export function AdminLayout() {
  const { me } = useMe();
  if (!me) return null;
  if (!me.is_admin) {
    return <EmptyState emoji="⛔️" title="دسترسی ندارید" />;
  }
  return <Outlet />;
}
