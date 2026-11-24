/** Iconos SVG en línea (trazo 2px, 24x24) para la navegación y acciones. */
import type { SVGProps } from 'react';

export type IconName =
  | 'dashboard'
  | 'tickets'
  | 'kanban'
  | 'approvals'
  | 'sla'
  | 'automations'
  | 'reports'
  | 'admin'
  | 'menu'
  | 'logout'
  | 'plus'
  | 'refresh'
  | 'filter'
  | 'chevronDown';

const PATHS: Record<IconName, string[]> = {
  dashboard: ['M3 3h7v9H3z', 'M14 3h7v5h-7z', 'M14 12h7v9h-7z', 'M3 16h7v5H3z'],
  tickets: [
    'M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v2a3 3 0 0 0 0 6v2a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-2a3 3 0 0 0 0-6z',
    'M13 5v2',
    'M13 11v2',
    'M13 17v2',
  ],
  kanban: ['M4 4h4v16H4z', 'M10 4h4v10h-4z', 'M16 4h4v13h-4z'],
  approvals: ['M9 11l3 3 8-8', 'M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9'],
  sla: ['M12 22a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M12 9v4l2.5 2.5', 'M10 2h4'],
  automations: ['M13 2 4 14h7l-1 8 9-12h-7z'],
  reports: ['M3 3v18h18', 'M7 15l4-4 3 3 5-6'],
  admin: [
    'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
    'M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  ],
  menu: ['M4 6h16', 'M4 12h16', 'M4 18h16'],
  logout: ['M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4', 'M16 17l5-5-5-5', 'M21 12H9'],
  plus: ['M12 5v14', 'M5 12h14'],
  refresh: ['M21 12a9 9 0 1 1-2.6-6.4L21 8', 'M21 3v5h-5'],
  filter: ['M3 5h18l-7 8.5V19l-4 2v-7.5z'],
  chevronDown: ['m6 9 6 6 6-6'],
};

export function Icon({ name, ...props }: { name: IconName } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={18}
      height={18}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {PATHS[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}
