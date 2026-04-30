import { useParams } from "react-router-dom";
import { TransactionsList } from "../../components/TransactionsList";

export function AdminUserTransactions() {
  const { id } = useParams<{ id: string }>();
  const userId = Number(id);
  return (
    <div>
      <h2>تراکنش‌های کاربر <span style={{ direction: "ltr" }}>{userId}</span></h2>
      <TransactionsList adminUserId={userId} />
    </div>
  );
}
