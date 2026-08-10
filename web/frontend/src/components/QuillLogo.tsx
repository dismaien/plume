import { QuillTextLogo } from "./QuillTextLogo";

export function QuillLogo({ className = "", onClick }: { className?: string; onClick?: () => void }) {
  const clickable = typeof onClick === "function";

  return (
    <div
      className={`${className} flex items-center gap-1.5 ${clickable ? "cursor-pointer hover:opacity-90 focus:opacity-90 outline-none" : ""}`}
      role={clickable ? ("button" as const) : undefined}
      tabIndex={clickable ? 0 : undefined}
      onClick={onClick}
      onKeyDown={(e) => {
        if (!clickable) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick?.();
        }
      }}
    >
      <QuillIcon className="h-7 w-auto sm:h-8 select-none" />
      <QuillTextLogo className="hidden sm:block" />
    </div>
  );
}

export function QuillIcon({ className = "" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 120 120"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-label="Plume Icon"
    >
      <defs>
        <linearGradient id="plume-icon-feather" x1="40%" y1="4%" x2="62%" y2="100%">
          <stop offset="0%" stopColor="#f2d681" />
          <stop offset="45%" stopColor="var(--scribe-gold, #c8a34b)" />
          <stop offset="100%" stopColor="#7f6122" />
        </linearGradient>
        <linearGradient id="plume-icon-shadow" x1="32%" y1="4%" x2="68%" y2="100%">
          <stop offset="0%" stopColor="var(--brand-400, #6daa75)" />
          <stop offset="100%" stopColor="var(--brand-900, #183a27)" />
        </linearGradient>
        <filter id="plume-icon-glow" x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="1.8" result="blur" />
          <feColorMatrix
            in="blur"
            type="matrix"
            values="0 0 0 0 0.80 0 0 0 0 0.64 0 0 0 0 0.24 0 0 0 0.65 0"
          />
          <feMerge>
            <feMergeNode />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      <g filter="url(#plume-icon-glow)">
        <path
          d="M75.7 9.6c-22.6 5.2-42.4 22.2-51.6 45.3 13.7-6.1 27.3-8.8 40.6-7.9-16.3 9.5-29.8 25-37.2 45 14.1-9.2 27-14.5 39-16.3-5.3 12.9-13.2 26.4-24.3 41.5 21.2-8.4 39.2-24 49.1-43.4 12.7-24.9 7-51-15.6-64.2Z"
          fill="url(#plume-icon-shadow)"
          opacity="0.5"
          transform="translate(4 4)"
        />
        <path
          d="M72.4 7.4c-22.6 5.2-42.4 22.2-51.6 45.3 13.7-6.1 27.3-8.8 40.6-7.9-16.3 9.5-29.8 25-37.2 45 14.1-9.2 27-14.5 39-16.3-5.3 12.9-13.2 26.4-24.3 41.5 21.2-8.4 39.2-24 49.1-43.4 12.7-24.9 7-51-15.6-64.2Z"
          fill="url(#plume-icon-feather)"
        />
        <g fill="none" stroke="#102d1b" strokeLinecap="round" strokeLinejoin="round" opacity="0.42">
          <path d="M68.5 28.3C53.2 29.8 40.8 35.8 31.6 45.9" strokeWidth="3.8" />
          <path d="M62.2 47.5C48.4 55.1 37.9 66 30.5 80.3" strokeWidth="3.8" />
          <path d="M62 72.3C53 81.2 46.1 91.7 41.3 103.8" strokeWidth="3.6" />
          <path d="M73 24.2C84.1 34.5 89.9 47.8 90.3 64" strokeWidth="3.8" />
          <path d="M66 52.9C77 62.4 83.4 73.5 85 86.2" strokeWidth="3.6" />
        </g>
        <path d="M73 9C66 34 58.1 72.8 42.4 115" fill="none" stroke="#102d1b" strokeLinecap="round" strokeWidth="4.8" opacity="0.5" />
        <path d="M72.2 8.2C65.2 33.3 57.3 72.1 41.7 114.4" fill="none" stroke="#f8e5ae" strokeLinecap="round" strokeWidth="2.8" opacity="0.95" />
        <path d="M40.8 113.6 29.4 117.2l8.1-17.1" fill="none" stroke="#f8e5ae" strokeLinecap="round" strokeLinejoin="round" strokeWidth="3.8" opacity="0.95" />
        <path d="M78.5 11.7 81.2 15.4l4.5.8-3.7 2.8-.5 4.5-2.8-3.6-4.5-.9 3.7-2.8.6-4.5Z" fill="#f5dda0" opacity="0.92" />
      </g>
    </svg>
  );
}
