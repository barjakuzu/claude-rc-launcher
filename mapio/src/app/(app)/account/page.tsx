import type { Metadata } from "next";
import { Download } from "lucide-react";
import { getAppContext } from "@/lib/app-context";
import { Card, PageTitle } from "@/components/app/card";
import { DeleteAccountForm, PreferencesForm } from "@/components/app/preferences-form";
import { deleteAccount, savePreferences } from "./actions";
import { signOutAction } from "@/app/(auth)/actions";

export const metadata: Metadata = { title: "Account" };

export default async function AccountPage() {
  const ctx = await getAppContext();
  return (
    <>
      <PageTitle title={ctx.t["nav.account"]} sub={ctx.user.email} />
      <div className="grid gap-5">
        <Card title="Preferences">
          <PreferencesForm profile={ctx.profile} action={savePreferences} submitLabel="Save preferences" />
        </Card>
        <div className="grid gap-5 md:grid-cols-2">
          <Card title="Your data">
            <p className="text-sm text-muted">Download everything MAPIO stores about you as JSON.</p>
            <a href="/api/account/export" className="btn btn-ghost mt-4">
              <Download className="h-4 w-4" aria-hidden /> Export my data
            </a>
          </Card>
          <Card title="Session">
            <p className="text-sm text-muted">Sign out of MAPIO on this browser. Paired-device credentials stay until revoked in Devices.</p>
            <form action={signOutAction} className="mt-4">
              <button className="btn btn-ghost">{ctx.t["nav.signout"]}</button>
            </form>
          </Card>
        </div>
        <Card title="Delete account" className="border-bad/30">
          <DeleteAccountForm action={deleteAccount} email={ctx.user.email} />
        </Card>
      </div>
    </>
  );
}
