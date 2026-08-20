interface RecordingDateTime {
    date: Date;
    time: string;
}

function parseRecordedWallClock(value: string) {
    const match = value.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/);
    if (!match) return null;

    const [, year, month, day, hour, minute, second = "0"] = match;
    const date = new Date(
        Number(year),
        Number(month) - 1,
        Number(day),
        Number(hour),
        Number(minute),
        Number(second),
    );

    return Number.isNaN(date.getTime()) ? null : date;
}

function parseCanonicalTitleWallClock(title?: string) {
    const match = title?.match(/^(\d{2})(\d{2})(\d{2})\s+(\d{2}):(\d{2})(?:\s|$)/);
    if (!match) return null;

    const [, year, month, day, hour, minute] = match;
    const date = new Date(
        2000 + Number(year),
        Number(month) - 1,
        Number(day),
        Number(hour),
        Number(minute),
    );

    return Number.isNaN(date.getTime()) ? null : date;
}

export function getRecordingDateTime(recordedAt?: string, createdAt?: string, title?: string): RecordingDateTime | null {
    // The canonical title prefix is the archive's preserved recording-local
    // wall clock. Older imports may have normalized recorded_at to UTC.
    let date: Date | null = parseCanonicalTitleWallClock(title);

    if (!date && recordedAt) {
        // Apple archive timestamps encode the recording-local wall clock, even
        // when the serialized value carries a UTC offset. Preserve those fields.
        date = parseRecordedWallClock(recordedAt);
    }

    if (!date && createdAt) {
        const importedAt = new Date(createdAt);
        if (!Number.isNaN(importedAt.getTime())) {
            date = importedAt;
        }
    }

    if (!date) return null;

    const pad = (value: number) => String(value).padStart(2, "0");
    return {
        date,
        time: `${pad(date.getHours())}:${pad(date.getMinutes())}`,
    };
}

export function formatRecordingOverviewDateTime(recordedAt?: string, createdAt?: string, title?: string) {
    const value = getRecordingDateTime(recordedAt, createdAt, title);
    if (!value) return "Date unavailable";

    const date = value.date.toLocaleDateString("en-US", {
        year: "numeric",
        month: "short",
        day: "numeric",
    });

    return `${date} · ${value.time}`;
}

export function formatRecordingSidebarDateTime(recordedAt?: string, createdAt?: string, title?: string) {
    const value = getRecordingDateTime(recordedAt, createdAt, title);
    if (!value) return "Date unavailable";

    const now = new Date();
    const date = value.date.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: value.date.getFullYear() === now.getFullYear() ? undefined : "numeric",
    });

    return `${date} · ${value.time}`;
}
