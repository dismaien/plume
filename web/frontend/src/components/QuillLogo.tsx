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
        <linearGradient id="plume-icon-feather" x1="18%" y1="6%" x2="88%" y2="92%">
          <stop offset="0%" stopColor="#f2d681" />
          <stop offset="45%" stopColor="var(--scribe-gold, #c8a34b)" />
          <stop offset="100%" stopColor="#7f6122" />
        </linearGradient>
        <linearGradient id="plume-icon-shadow" x1="22%" y1="10%" x2="85%" y2="95%">
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
        <g fill="none" stroke="url(#plume-icon-shadow)" strokeLinecap="round" strokeLinejoin="round" opacity="0.58">
          <path d="M89 10C76 27 67 43 58 62 50 79 41 94 27 111" strokeWidth="8" />
          <path d="M80 25C66 27 54 34 43 45" strokeWidth="7" />
          <path d="M70 44C56 48 44 56 33 69" strokeWidth="7" />
          <path d="M59 64C49 70 40 79 32 90" strokeWidth="7" />
          <path d="M80 25C91 31 98 41 100 54" strokeWidth="7" />
          <path d="M70 44C81 53 86 65 82 79" strokeWidth="7" />
          <path d="M59 64C67 73 69 85 63 97" strokeWidth="7" />
        </g>
        <g fill="none" stroke="url(#plume-icon-feather)" strokeLinecap="round" strokeLinejoin="round">
          <path d="M87 8C74 25 65 41 56 60 48 77 39 92 25 109" strokeWidth="5.2" />
          <path d="M78 23C64 25 52 32 41 43" strokeWidth="5" />
          <path d="M68 42C54 46 42 54 31 67" strokeWidth="5" />
          <path d="M57 62C47 68 38 77 30 88" strokeWidth="5" />
          <path d="M78 23C89 29 96 39 98 52" strokeWidth="5" />
          <path d="M68 42C79 51 84 63 80 77" strokeWidth="5" />
          <path d="M57 62C65 71 67 83 61 95" strokeWidth="5" />
        </g>
        <path d="M87.3 16.2 90.4 20.3l4.9 1-4.1 3.1-.4 5-3.2-4-5-1.1 4.2-3.1.5-5Z" fill="#f5dda0" opacity="0.92" />
        <path
          d="M24.5 108.7c-2.4 1.4-5.2 2.6-8.4 3.5 1.6-2.8 3.6-5.1 6-6.9"
          fill="none"
          stroke="#f5dda0"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="3.6"
          opacity="0.9"
        />
      </g>
    </svg>
  );
}
