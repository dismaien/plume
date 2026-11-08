import { useMemo, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowRight,
  BookOpenText,
  CalendarDays,
  CheckSquare,
  CircleHelp,
  Clock3,
  GitBranch,
  Lightbulb,
  Loader2,
  MessageSquareText,
  RefreshCw,
  Search,
  Users,
} from "lucide-react";
import { Header } from "@/components/Header";
import { MainLayout } from "@/components/layout/MainLayout";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/features/auth/hooks/useAuth";
import { cn } from "@/lib/utils";

type ReviewRange = "last" | "7d" | "30d";

interface ReviewEvidence {
  source_id: string;
  start_seconds?: number;
}

interface ReviewInsight {
  text: string;
  evidence: ReviewEvidence[];
}

interface ReviewSection {
  id: string;
  title: string;
  items: ReviewInsight[];
}

interface ReviewSource {
  id: string;
  job_id: string;
  title: string;
  recorded_at: string;
  duration_ms: number;
  speakers: string[];
  has_unclear_speakers: boolean;
}

interface ReviewResponse {
  generated_at: string;
  since: string;
  until: string;
  recording_count: number;
  total_matching: number;
  model?: string;
  sections: ReviewSection[];
  sources: ReviewSource[];
}

interface AskResponse {
  answer: string;
  evidence: ReviewEvidence[];
  sources: ReviewSource[];
  model?: string;
}

const LAST_REVIEW_KEY = "plume:last-conversation-review";

const sectionIcons: Record<string, typeof Lightbulb> = {
  themes: Lightbulb,
  people: Users,
  decisions: CheckSquare,
  open_loops: Clock3,
  connections: GitBranch,
  unidentified: CircleHelp,
};

function rangeWindow(range: ReviewRange) {
  const until = new Date();
  let since: Date;
  if (range === "last") {
    const stored = window.localStorage.getItem(LAST_REVIEW_KEY);
    const parsed = stored ? new Date(stored) : new Date(Number.NaN);
    since = Number.isNaN(parsed.getTime()) ? new Date(until.getTime() - 7 * 86_400_000) : parsed;
  } else {
    since = new Date(until.getTime() - (range === "30d" ? 30 : 7) * 86_400_000);
  }
  if (since >= until) {
    since = new Date(until.getTime() - 60_000);
  }
  return { since: since.toISOString(), until: until.toISOString() };
}

function formatTimestamp(seconds?: number) {
  if (seconds === undefined || !Number.isFinite(seconds)) return "";
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`
    : `${minutes}:${String(secs).padStart(2, "0")}`;
}

function formatPeriod(since: string, until: string) {
  const start = new Date(since);
  const end = new Date(until);
  return `${start.toLocaleDateString(undefined, { month: "short", day: "numeric" })} – ${end.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`;
}

export function ReviewPage() {
  const navigate = useNavigate();
  const { getAuthHeaders } = useAuth();
  const [range, setRange] = useState<ReviewRange>("7d");
  const [review, setReview] = useState<ReviewResponse | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [reviewError, setReviewError] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [isAsking, setIsAsking] = useState(false);
  const [askError, setAskError] = useState("");

  const reviewSources = useMemo(
    () => new Map((review?.sources ?? []).map((source) => [source.id, source])),
    [review?.sources],
  );
  const answerSources = useMemo(
    () => new Map((answer?.sources ?? []).map((source) => [source.id, source])),
    [answer?.sources],
  );

  const postJSON = async <T,>(path: string, body: object): Promise<T> => {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      body: JSON.stringify(body),
    });
    const result = await response.json() as T & { error?: string };
    if (!response.ok) {
      throw new Error(result.error || `Request failed (${response.status})`);
    }
    return result;
  };

  const generateReview = async () => {
    setIsGenerating(true);
    setReviewError("");
    setAnswer(null);
    try {
      const period = rangeWindow(range);
      const result = await postJSON<ReviewResponse>("/api/v1/review/generate", {
        ...period,
        max_recordings: 18,
      });
      setReview(result);
      window.localStorage.setItem(LAST_REVIEW_KEY, result.generated_at);
    } catch (error) {
      setReviewError(error instanceof Error ? error.message : "Review generation failed");
    } finally {
      setIsGenerating(false);
    }
  };

  const askArchive = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || isAsking) return;
    setIsAsking(true);
    setAskError("");
    try {
      const result = await postJSON<AskResponse>("/api/v1/review/ask", {
        ...rangeWindow(range),
        max_recordings: 14,
        question: trimmed,
      });
      setAnswer(result);
    } catch (error) {
      setAskError(error instanceof Error ? error.message : "Archive question failed");
    } finally {
      setIsAsking(false);
    }
  };

  const openSource = (source: ReviewSource, seconds?: number) => {
    const query = seconds === undefined ? "" : `?t=${Math.max(0, seconds)}`;
    navigate(`/audio/${source.job_id}${query}`, { state: { from: "/review" } });
  };

  const renderEvidence = (evidence: ReviewEvidence[], sources: Map<string, ReviewSource>) => (
    <div className="mt-3 flex flex-wrap gap-2">
      {evidence.map((ref, index) => {
        const source = sources.get(ref.source_id);
        if (!source) return null;
        const timestamp = formatTimestamp(ref.start_seconds);
        return (
          <button
            key={`${ref.source_id}-${timestamp}-${index}`}
            type="button"
            onClick={() => openSource(source, ref.start_seconds)}
            className="inline-flex max-w-full items-center gap-1.5 border-b border-[var(--brand-solid)]/40 pb-0.5 text-left text-xs font-medium text-[var(--brand-solid)] hover:border-[var(--brand-solid)]"
            title={`Open ${source.title}${timestamp ? ` at ${timestamp}` : ""}`}
          >
            <span className="truncate">{source.title}</span>
            {timestamp && <span className="shrink-0 font-mono text-[var(--text-secondary)]">{timestamp}</span>}
            <ArrowRight className="h-3 w-3 shrink-0" />
          </button>
        );
      })}
    </div>
  );

  const populatedSections = review?.sections.filter((section) => section.items.length > 0) ?? [];

  return (
    <MainLayout className="min-h-screen bg-[var(--bg-main)]" header={<Header homePath="/" />}>
      <div className="mx-auto max-w-5xl space-y-6">
        <section className="obsidian-pane overflow-hidden p-5 sm:p-7">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
            <div className="min-w-0">
              <div className="mb-3 flex items-center gap-2 text-sm font-medium text-[var(--brand-solid)]">
                <BookOpenText className="h-4 w-4" />
                Conversation archive
              </div>
              <h1 className="text-2xl font-bold text-[var(--text-primary)] sm:text-3xl">Review recent conversations</h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--text-secondary)]">
                Find recurring themes, commitments, open loops, and connections. Analysis stays on this Mac through your local Ollama model.
              </p>
            </div>

            <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
              <div className="inline-flex h-10 items-center rounded-[var(--radius-btn)] border border-[var(--border-subtle)] bg-[var(--bg-muted-pane)] p-1" aria-label="Review period">
                {(["last", "7d", "30d"] as ReviewRange[]).map((value) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setRange(value)}
                    className={cn(
                      "h-8 px-3 text-sm font-medium text-[var(--text-secondary)] transition-colors",
                      range === value && "rounded-[calc(var(--radius-btn)-3px)] border border-[var(--border-subtle)] bg-[var(--bg-card)] text-[var(--text-primary)] shadow-sm",
                    )}
                  >
                    {value === "last" ? "Since last" : value === "7d" ? "7 days" : "30 days"}
                  </button>
                ))}
              </div>
              <Button variant="brand" onClick={generateReview} disabled={isGenerating} className="h-10">
                {isGenerating ? <Loader2 className="animate-spin" /> : review ? <RefreshCw /> : <Lightbulb />}
                {isGenerating ? "Reviewing..." : review ? "Refresh" : "Generate review"}
              </Button>
            </div>
          </div>
        </section>

        {reviewError && (
          <div role="alert" className="border-l-2 border-[var(--error)] bg-[var(--error)]/5 px-4 py-3 text-sm text-[var(--text-primary)]">
            {reviewError}
          </div>
        )}

        {!review && !isGenerating && (
          <section className="py-12 text-center">
            <CalendarDays className="mx-auto h-8 w-8 text-[var(--text-tertiary)]" />
            <h2 className="mt-4 text-lg font-semibold text-[var(--text-primary)]">Choose a period and generate a review</h2>
            <p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-[var(--text-secondary)]">
              Plume uses saved summaries, confirmed speaker names, and timestamped excerpts. Nothing is uploaded by this feature.
            </p>
          </section>
        )}

        {review && (
          <section aria-live="polite" className="space-y-0">
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-[var(--border-subtle)] pb-4 text-sm text-[var(--text-secondary)]">
              <span className="font-medium text-[var(--text-primary)]">{formatPeriod(review.since, review.until)}</span>
              <span>{review.recording_count} recording{review.recording_count === 1 ? "" : "s"} reviewed</span>
              {review.total_matching > review.recording_count && <span>{review.total_matching - review.recording_count} newer candidates omitted to fit the local model</span>}
              {review.model && <span className="font-mono text-xs">{review.model}</span>}
            </div>

            {review.recording_count === 0 ? (
              <div className="py-12 text-center text-sm text-[var(--text-secondary)]">No completed recordings were found in this period.</div>
            ) : populatedSections.length === 0 ? (
              <div className="py-12 text-center text-sm text-[var(--text-secondary)]">The local model did not return evidence-backed findings for this period.</div>
            ) : (
              populatedSections.map((section) => {
                const Icon = sectionIcons[section.id] ?? MessageSquareText;
                return (
                  <div key={section.id} className="grid gap-4 border-b border-[var(--border-subtle)] py-6 md:grid-cols-[220px_minmax(0,1fr)]">
                    <h2 className="flex items-center gap-2 self-start text-base font-semibold text-[var(--text-primary)]">
                      <Icon className="h-4 w-4 text-[var(--brand-solid)]" />
                      {section.title}
                    </h2>
                    <div className="divide-y divide-[var(--border-subtle)]">
                      {section.items.map((item, index) => (
                        <article key={index} className="py-4 first:pt-0 last:pb-0">
                          <p className="text-sm leading-6 text-[var(--text-primary)]">{item.text}</p>
                          {renderEvidence(item.evidence, reviewSources)}
                        </article>
                      ))}
                    </div>
                  </div>
                );
              })
            )}
          </section>
        )}

        <section className="obsidian-pane p-5 sm:p-7">
          <div className="flex items-start gap-3">
            <Search className="mt-1 h-5 w-5 shrink-0 text-[var(--brand-solid)]" />
            <div className="min-w-0 flex-1">
              <h2 className="text-lg font-semibold text-[var(--text-primary)]">Ask across recordings</h2>
              <p className="mt-1 text-sm text-[var(--text-secondary)]">Ask about a person, topic, decision, or thread across the selected period.</p>
            </div>
          </div>
          <form onSubmit={askArchive} className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-end">
            <Textarea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="What have I said about moving, and what is still unresolved?"
              className="min-h-20 flex-1 resize-y bg-[var(--bg-main)]"
              maxLength={1_000}
            />
            <Button type="submit" variant="outline" disabled={isAsking || !question.trim()} className="h-10 sm:self-end">
              {isAsking ? <Loader2 className="animate-spin" /> : <MessageSquareText />}
              {isAsking ? "Searching..." : "Ask"}
            </Button>
          </form>

          {askError && <p role="alert" className="mt-4 text-sm text-[var(--error)]">{askError}</p>}
          {answer && (
            <div className="mt-6 border-t border-[var(--border-subtle)] pt-5" aria-live="polite">
              <p className="whitespace-pre-wrap text-sm leading-7 text-[var(--text-primary)]">{answer.answer}</p>
              {renderEvidence(answer.evidence, answerSources)}
            </div>
          )}
        </section>
      </div>
    </MainLayout>
  );
}
