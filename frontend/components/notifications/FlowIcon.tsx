/** Iconos SVG en línea del módulo flows (mismo estilo que components/icons.tsx: trazo 2px, 24x24). */
import type { SVGProps } from 'react';

export type FlowIconName =
  | 'bell'
  | 'check'
  | 'checkAll'
  | 'x'
  | 'user'
  | 'comment'
  | 'alert'
  | 'clock'
  | 'bolt'
  | 'shield'
  | 'trash'
  | 'play'
  | 'copy'
  | 'arrowRight';

const PATHS: Record<FlowIconName, string[]> = {
  bell: ['M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9', 'M13.7 21a2 2 0 0 1-3.4 0'],
  check: ['M20 6 9 17l-5-5'],
  checkAll: ['M18 6 7 17l-5-5', 'M22 10l-7.5 7.5L13 16'],
  x: ['M18 6 6 18', 'M6 6l12 12'],
  user: ['M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2', 'M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z'],
  comment: ['M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z'],
  alert: ['M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z', 'M12 9v4', 'M12 17h.01'],
  clock: ['M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z', 'M12 6v6l4 2'],
  bolt: ['M13 2 4 14h7l-1 8 9-12h-7z'],
  shield: ['M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z', 'M9 12l2 2 4-4'],
  trash: ['M3 6h18', 'M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2', 'M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6'],
  play: ['M6 4l14 8-14 8z'],
  copy: ['M9 9h11v11H9z', 'M5 15H4V4h11v1'],
  arrowRight: ['M5 12h14', 'M13 6l6 6-6 6'],
};

export function FlowIcon({ name, ...props }: { name: FlowIconName } & SVGProps<SVGSVGElement>) {
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
