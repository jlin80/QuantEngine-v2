import {
  Activity,
  Ban,
  Bell,
  Boxes,
  BrainCircuit,
  Briefcase,
  CandlestickChart,
  Coins,
  FileText,
  FlaskConical,
  Gauge,
  HeartPulse,
  LayoutDashboard,
  NotebookText,
  ScrollText,
  Settings,
  ShieldCheck,
  Waves,
} from "lucide-react";

export type NavIcon = React.ComponentType<{ className?: string }>;

export interface NavItem {
  label: string;
  href: string;
  icon: NavIcon;
  /** Screens landing in later Fase 8 increments render as "soon". */
  enabled: boolean;
}

export interface NavSection {
  title: string;
  items: NavItem[];
}

export const NAV: NavSection[] = [
  {
    title: "Live",
    items: [
      { label: "Overview", href: "/", icon: LayoutDashboard, enabled: true },
      { label: "Operations", href: "/operations", icon: Briefcase, enabled: true },
      { label: "Market", href: "/market", icon: CandlestickChart, enabled: true },
    ],
  },
  {
    title: "Analysis",
    items: [
      { label: "Strategies", href: "/strategies", icon: Boxes, enabled: true },
      { label: "Order Flow", href: "/orderflow", icon: Waves, enabled: true },
      { label: "Machine Learning", href: "/ml", icon: BrainCircuit, enabled: true },
      { label: "Backtesting", href: "/backtesting", icon: FlaskConical, enabled: true },
    ],
  },
  // Edge Intelligence (bloques 1-15). Tenían backend, tests y ADR desde el
  // 05/08 pero ninguna pantalla: son los diagnósticos que responden por qué el
  // motor no gana, y vivían sólo para quien supiera hacer `curl`.
  {
    title: "Edge Intelligence",
    items: [
      { label: "Edge Research", href: "/edge", icon: Activity, enabled: true },
      { label: "Why Not Trade", href: "/rejections", icon: Ban, enabled: true },
      { label: "Costes", href: "/costs", icon: Coins, enabled: true },
      { label: "Diagnóstico", href: "/diagnostics", icon: Gauge, enabled: true },
    ],
  },
  {
    title: "Operations",
    items: [
      { label: "Trade Journal", href: "/journal", icon: NotebookText, enabled: true },
      { label: "Logs", href: "/logs", icon: ScrollText, enabled: true },
      { label: "Health", href: "/health", icon: HeartPulse, enabled: true },
      { label: "Production", href: "/production", icon: ShieldCheck, enabled: true },
      { label: "Reports", href: "/reports", icon: FileText, enabled: true },
      { label: "Alerts", href: "/alerts", icon: Bell, enabled: true },
      { label: "Settings", href: "/settings", icon: Settings, enabled: true },
    ],
  },
];
