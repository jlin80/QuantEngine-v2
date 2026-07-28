import { PageHeader } from "@/components/common/page-header";
import { ConfigForm } from "@/components/settings/config-form";
import { DiscordCard, NotionCard } from "@/components/settings/integrations";
import { RestartEngineCard } from "@/components/settings/restart-engine";

export default function SettingsPage() {
  return (
    <div className="space-y-4">
      <PageHeader
        title="Settings"
        subtitle="Runtime configuration and integrations — every change is audited"
      />
      <ConfigForm />
      <div className="grid gap-4 md:grid-cols-2">
        <DiscordCard />
        <NotionCard />
      </div>
      <RestartEngineCard />
    </div>
  );
}
