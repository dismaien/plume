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
      <QuillIcon className="h-9 w-auto sm:h-10 select-none" />
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
        <linearGradient id="plume-quill-gold" x1="18%" y1="94%" x2="86%" y2="8%">
          <stop offset="0%" stopColor="#7b6124" />
          <stop offset="42%" stopColor="var(--scribe-gold, #c8a34b)" />
          <stop offset="100%" stopColor="#f7e4a7" />
        </linearGradient>
        <linearGradient id="plume-quill-ink" x1="0%" y1="100%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#15291d" />
          <stop offset="58%" stopColor="var(--brand-700, #2c5a3a)" />
          <stop offset="100%" stopColor="#7b6124" />
        </linearGradient>
        <filter id="plume-quill-shadow" x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0.8" dy="1.4" stdDeviation="1.1" floodColor="#07160d" floodOpacity="0.42" />
        </filter>
      </defs>

      <g
        strokeLinecap="round"
        strokeLinejoin="round"
        filter="url(#plume-quill-shadow)"
      >
        <path
          d="M14 103.2c14.8-9.4 27.7-.6 41-5.7 12.2-4.7 20.1-8.6 34.7-.4"
          stroke="url(#plume-quill-ink)"
          strokeWidth="3.1"
          opacity="0.6"
        />
        <path
          d="M19.4 103.5c11.4-3.3 21.5 1.1 32.9-3.1"
          stroke="#efd891"
          strokeWidth="1.8"
          opacity="0.82"
        />

        <path
          d="M87.6 8.4c-20.1 2.5-37.3 12.6-49.3 28.9-9.4 12.8-14.9 28-17 45.5 11.8-10.6 25.2-16.4 40.3-17.3-9.9 8.3-18.3 19.8-25.1 34.5 22.4-8.3 39.2-21.3 50.2-39 11.8-18.9 12.1-39.2.9-52.6Z"
          fill="url(#plume-quill-gold)"
          stroke="url(#plume-quill-gold)"
          strokeWidth="1.2"
          opacity="0.48"
        />
        <path
          d="M87.6 8.4c-20.1 2.5-37.3 12.6-49.3 28.9-9.4 12.8-14.9 28-17 45.5 11.8-10.6 25.2-16.4 40.3-17.3-9.9 8.3-18.3 19.8-25.1 34.5 22.4-8.3 39.2-21.3 50.2-39 11.8-18.9 12.1-39.2.9-52.6Z"
          fill="none"
          stroke="#102a1b"
          strokeWidth="1.4"
          opacity="0.58"
        />
        <path
          d="M38.4 91.8c7.6-22.3 16.1-47.9 32.1-66.9 5.2-6.1 10.9-10.8 17.2-14.2"
          stroke="#0d2216"
          strokeWidth="6"
          opacity="0.42"
          fill="none"
        />
        <path
          d="M37.2 91.3c8.2-23.8 16.9-49.4 32.8-67.8 5.1-5.9 10.9-10.4 17.8-13.7"
          stroke="url(#plume-quill-gold)"
          strokeWidth="3.8"
          fill="none"
        />
        <path
          d="M87.8 9.8c-18.8 2.8-36.3 13-50.4 29.2"
          stroke="#102a1b"
          strokeWidth="1.6"
          opacity="0.62"
          fill="none"
        />

        <g stroke="#f3dc99" strokeWidth="2.6" opacity="0.96">
          <path d="M79.8 15.9c-12.2 3.2-22.7 9-31.6 17.5" fill="none" />
          <path d="M75.8 23.7C60.5 29.8 46.9 39 35 51.5" fill="none" />
          <path d="M70.8 34.1C53.4 42.4 39.2 54.5 28.1 70.3" fill="none" />
          <path d="M64.5 48.5C52.8 56.6 43.4 66.9 36.2 79.3" fill="none" />
          <path d="M56.2 67.2c-5.6 5.2-10 11.5-13.3 18.9" fill="none" />
        </g>
        <g stroke="#183624" strokeWidth="1.15" opacity="0.7">
          <path d="M79.5 16.4c-10.4 4.1-19.3 9.8-26.7 17.1" fill="none" />
          <path d="M74.3 25.6C60 32 47.9 41 37.9 52.5" fill="none" />
          <path d="M68.6 37.2C53.8 45.2 41.9 56.5 32.8 71" fill="none" />
          <path d="M61.6 53C52 60.8 44.6 70.1 39.3 81" fill="none" />
        </g>

        <g stroke="#d1ad56" strokeWidth="2.2" opacity="0.88">
          <path d="M71.7 24.1c9.3 4 16.6 9.6 22 16.8" fill="none" />
          <path d="M66.8 35.6c8.5 4.8 15.2 10.9 20.1 18.2" fill="none" />
          <path d="M60.3 49.9c6.8 4.8 11.8 10.7 15.1 17.5" fill="none" />
          <path d="M52.8 66.9c4.4 3.7 7.5 8 9.4 12.7" fill="none" />
        </g>
        <g stroke="#183624" strokeWidth="1.05" opacity="0.62">
          <path d="M72.8 24.7c7.8 3.8 14.3 9.2 19.3 16.3" fill="none" />
          <path d="M66.9 36.2c7.9 4.8 13.8 10.9 17.8 18.3" fill="none" />
          <path d="M59.7 50.3c5.7 4.8 9.9 10.4 12.5 16.8" fill="none" />
        </g>

        <path
          d="M38 91.5l-9.5 16.2 17.2-9.1"
          stroke="#0d2216"
          strokeWidth="5.2"
          opacity="0.38"
          fill="none"
        />
        <path
          d="M37.3 90.8l-8.6 16.3 16.8-9.7"
          stroke="url(#plume-quill-gold)"
          strokeWidth="3.2"
          fill="none"
        />
        <path
          d="M27.8 108.4l-6.9 2.3 3.8-6.6"
          stroke="#efd891"
          strokeWidth="2.2"
          opacity="0.9"
          fill="none"
        />
      </g>
    </svg>
  );
}
