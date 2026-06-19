import { AccountWorkspace } from '@/components/AccountWorkspace';
import { AppHeader } from '@/components/AppHeader';

export default function AccountPage() {
  return (
    <main className="shell">
      <AppHeader title="Account" />
      <AccountWorkspace />
    </main>
  );
}
