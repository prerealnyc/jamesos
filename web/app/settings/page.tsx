import { Card } from "@/components/ui";

export default function SettingsPage() {
  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="text-muted-foreground text-sm mt-1">Profile, social connections, integrations.</p>
      </header>
      <Card>
        <p className="text-sm text-muted-foreground">
          Profile, social connections, and integration status are already live on the backend
          (<code>/api/profile</code>, <code>/api/connections</code>, <code>/api/integrations</code>).
          This native settings page is queued in the greenfield rebuild — Brand voice &amp; rules
          (the product&apos;s core) was prioritized first.
        </p>
      </Card>
    </div>
  );
}
