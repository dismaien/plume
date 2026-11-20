import { useState, useEffect, useRef, useCallback } from 'react';

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

interface MatchRange {
  range: Range;
  node: Node;
}

interface UseTranscriptSearchResult {
  matchCount: number;
  activeMatchIndex: number;
  goToNext: () => void;
  goToPrevious: () => void;
  clearHighlights: () => void;
}

export function useTranscriptSearch(
  searchQuery: string,
  containerRef: React.RefObject<HTMLDivElement | null>,
  transcriptMode: string,
): UseTranscriptSearchResult {
  const [matchCount, setMatchCount] = useState(0);
  const [activeMatchIndex, setActiveMatchIndex] = useState(0);
  const matchesRef = useRef<MatchRange[]>([]);

  const activateMatch = useCallback((index: number) => {
    const matches = matchesRef.current;
    if (matches.length === 0) return;

    const normalizedIndex = ((index % matches.length) + matches.length) % matches.length;
    const active = matches[normalizedIndex];
    setActiveMatchIndex(normalizedIndex);

    if (typeof CSS !== 'undefined' && CSS.highlights) {
      CSS.highlights.set('search-match-active', new Highlight(active.range));
    }

    active.range.startContainer.parentElement?.scrollIntoView({
      behavior: 'smooth',
      block: 'center',
    });
  }, []);

  // Build matches and set CSS highlights
  const runSearch = useCallback(() => {
    // Clear previous highlights
    if (typeof CSS !== 'undefined' && CSS.highlights) {
      CSS.highlights.delete('search-match');
      CSS.highlights.delete('search-match-active');
    }

    const query = searchQuery.trim();
    if (!query || !containerRef.current) {
      matchesRef.current = [];
      setMatchCount(0);
      setActiveMatchIndex(0);
      return;
    }

    const matches: MatchRange[] = [];
    const walker = document.createTreeWalker(
      containerRef.current,
      NodeFilter.SHOW_TEXT,
    );

    const pattern = new RegExp(escapeRegExp(query), 'gi');

    let node: Text | null;
    while ((node = walker.nextNode() as Text | null)) {
      // Only search within transcript text containers
      const parent = node.parentElement;
      if (!parent?.closest('[data-transcript-text]')) continue;

      const text = node.textContent || '';
      let match: RegExpExecArray | null;
      pattern.lastIndex = 0;
      while ((match = pattern.exec(text)) !== null) {
        try {
          const range = new Range();
          range.setStart(node, match.index);
          range.setEnd(node, match.index + match[0].length);
          matches.push({ range, node });
        } catch {
          // Ignore invalid ranges
        }
      }
    }

    matchesRef.current = matches;
    setMatchCount(matches.length);

    if (matches.length === 0) {
      setActiveMatchIndex(0);
      return;
    }

    // Apply highlights
    if (typeof CSS !== 'undefined' && CSS.highlights) {
      const allRanges = matches.map(m => m.range);
      CSS.highlights.set('search-match', new Highlight(...allRanges));
    }

    // Activating explicitly is important when the first result is still index 0.
    // A state-only effect would not run for an unchanged index or a single match.
    activateMatch(0);
  }, [activateMatch, searchQuery, containerRef]);

  // Re-run search when query or mode changes (mode change rebuilds DOM)
  useEffect(() => {
    // Use rAF to let React finish rendering new DOM after mode change
    const id = requestAnimationFrame(() => runSearch());
    return () => cancelAnimationFrame(id);
  }, [runSearch, transcriptMode]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (typeof CSS !== 'undefined' && CSS.highlights) {
        CSS.highlights.delete('search-match');
        CSS.highlights.delete('search-match-active');
      }
    };
  }, []);

  const goToNext = useCallback(() => {
    if (matchesRef.current.length === 0) return;
    activateMatch(activeMatchIndex + 1);
  }, [activateMatch, activeMatchIndex]);

  const goToPrevious = useCallback(() => {
    if (matchesRef.current.length === 0) return;
    activateMatch(activeMatchIndex - 1);
  }, [activateMatch, activeMatchIndex]);

  const clearHighlights = useCallback(() => {
    if (typeof CSS !== 'undefined' && CSS.highlights) {
      CSS.highlights.delete('search-match');
      CSS.highlights.delete('search-match-active');
    }
    matchesRef.current = [];
    setMatchCount(0);
    setActiveMatchIndex(0);
  }, []);

  return {
    matchCount,
    activeMatchIndex,
    goToNext,
    goToPrevious,
    clearHighlights,
  };
}

export { escapeRegExp };
