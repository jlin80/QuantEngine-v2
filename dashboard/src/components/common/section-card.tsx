import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export function SectionCard({
  title,
  icon,
  action,
  children,
  className,
  contentClassName,
}: {
  title: React.ReactNode;
  icon?: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  contentClassName?: string;
}) {
  return (
    <Card className={cn("h-full", className)}>
      <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0 border-b">
        <CardTitle className="flex items-center gap-2 text-sm [&>svg]:size-4 [&>svg]:text-muted-foreground">
          {icon}
          {title}
        </CardTitle>
        {action}
      </CardHeader>
      <CardContent className={cn("pt-4", contentClassName)}>{children}</CardContent>
    </Card>
  );
}
