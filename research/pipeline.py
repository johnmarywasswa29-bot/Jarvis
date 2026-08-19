"""Multi-step research pipeline for Jarvis.

Implements SEARCH → FETCH → EXTRACT → IDENTIFY GAPS → SEARCH MORE → SYNTHESIZE
with hard limits and safety guarantees.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any, Optional
from urllib.parse import urlparse

from modules.config import JarvisConfig
from modules.llm_providers import get_llm_provider, LLMProvider
from modules.tools import ToolRegistry
from research.fetcher import fetch_url, FetchResult
from research.extractor import extract_content, ExtractionResult
from research.fetcher import search_web, DEFAULT_SEARCH_TIMEOUT

# Optional DDGS import for testing
try:
    from ddgs import DDGS
except ImportError:
    DDGS = None

logger = logging.getLogger("research.pipeline")


# =============================================================================
# Configuration / Limits
# =============================================================================

@dataclass(frozen=True)
class ResearchLimits:
    """Hard limits for research pipeline to prevent uncontrolled loops."""
    max_steps: int = 5
    max_searches: int = 3
    max_fetches: int = 5
    max_content_per_page: int = 8000  # characters
    max_total_content: int = 40000   # characters across all sources
    overall_timeout_s: float = 60.0
    max_concurrent_jobs: int = 1


DEFAULT_LIMITS = ResearchLimits()


# =============================================================================
# Data Structures
# =============================================================================

@dataclass
class ResearchSource:
    """A source discovered and optionally fetched during research."""
    title: str
    url: str
    search_query: str
    fetch_status: str  # "success", "failed", "skipped"
    extracted_text: str = ""
    fetch_error: str = ""
    extraction_method: str = ""
    metadata: dict = field(default_factory=dict)
    rank: int = 0  # relevance rank from search

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "search_query": self.search_query,
            "fetch_status": self.fetch_status,
            "extracted_text": self.extracted_text,
            "fetch_error": self.fetch_error,
            "extraction_method": self.extraction_method,
            "metadata": self.metadata,
            "rank": self.rank,
        }


@dataclass
class ResearchStep:
    """Record of a single research step."""
    step_number: int
    action: str  # "search", "fetch", "extract", "identify_gaps", "synthesize"
    query: str = ""
    results_count: int = 0
    urls_fetched: int = 0
    gaps_identified: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_number": self.step_number,
            "action": self.action,
            "query": self.query,
            "results_count": self.results_count,
            "urls_fetched": self.urls_fetched,
            "gaps_identified": self.gaps_identified,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


@dataclass
class ResearchFindings:
    """Structured result of multi-step research."""
    query: str
    sources: list[ResearchSource] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    synthesis: str = ""
    confidence: float = 0.0
    gaps: list[str] = field(default_factory=list)
    research_steps: list[ResearchStep] = field(default_factory=list)
    duration_s: float = 0.0
    research_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    started_at: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "sources": [s.to_dict() for s in self.sources],
            "findings": self.findings,
            "synthesis": self.synthesis,
            "confidence": self.confidence,
            "gaps": self.gaps,
            "research_steps": [s.to_dict() for s in self.research_steps],
            "duration_s": self.duration_s,
            "research_id": self.research_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    def get_citations(self) -> list[dict[str, Any]]:
        """Extract citation info from sources that were successfully fetched."""
        citations = []
        for i, src in enumerate(self.sources):
            if src.fetch_status == "success" and src.extracted_text:
                citations.append({
                    "index": i + 1,
                    "title": src.title or src.url,
                    "url": src.url,
                    "search_query": src.search_query,
                    "extraction_method": src.extraction_method,
                })
        return citations


# =============================================================================
# Synthesizer Abstraction (Phase 9B dependency-injection seam)
# =============================================================================
# ResearchPipeline delegates the LLM-dependent reasoning steps (gap detection
# and synthesis-with-citations) to a ResearchSynthesizer. Production uses
# LLMResearchSynthesizer, a faithful 1:1 re-host of the Phase 9A inline logic
# that wraps the EXISTING ``get_llm_provider`` architecture unchanged. Tests
# inject a deterministic FakeSynthesizer so research behavior can be exercised
# without a live LLM or internet. This does NOT replace ToolRegistry, the LLM
# provider architecture, add LangGraph, or change Phase 9A behavior.

@dataclass
class ResearchSynthesisResult:
    """Structured output of a synthesis step."""
    synthesis: str = ""
    confidence: float = 0.0
    gaps: list[str] = field(default_factory=list)


class ResearchSynthesisError(Exception):
    """Raised by a ResearchSynthesizer when gap analysis or synthesis fails.

    Carries ``rate_limited`` so callers can distinguish transient provider
    limits (HTTP 429, quota) from other provider failures and degrade
    gracefully without crashing the pipeline.
    """

    def __init__(self, message: str, *, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.rate_limited = rate_limited


class ResearchSynthesizer:
    """Abstraction for the LLM-dependent reasoning steps of research.

    Implemented by :class:`LLMResearchSynthesizer` in production (which wraps
    the existing ``get_llm_provider`` architecture unchanged) and by a
    deterministic fake in tests. This seam lets Phase 9B exercise gap
    detection, iterative search, and citation synthesis without a live LLM.
    """

    def identify_gaps(self, question: str, sources: list[ResearchSource]) -> list[str]:
        raise NotImplementedError

    def synthesize(
        self,
        question: str,
        sources: list[ResearchSource],
        citations: list[dict[str, Any]],
    ) -> ResearchSynthesisResult:
        raise NotImplementedError


class LLMResearchSynthesizer(ResearchSynthesizer):
    """Production synthesizer: faithful 1:1 re-host of Phase 9A inline gap
    detection and synthesis logic, delegating to ``get_llm_provider``.

    No prompts, parsing, or fallback heuristics are changed from Phase 9A.
    """

    def __init__(self, config: Optional[JarvisConfig] = None) -> None:
        self.config = config or JarvisConfig()
        self._llm_provider: Optional[LLMProvider] = None

    def _get_llm(self) -> Optional[LLMProvider]:
        if self._llm_provider is None:
            try:
                self._llm_provider = get_llm_provider(self.config)
            except Exception as e:
                logger.warning("Failed to initialize LLM provider: %s", e)
                self._llm_provider = None
        return self._llm_provider

    def identify_gaps(self, question: str, sources: list[ResearchSource]) -> list[str]:
        context_parts = []
        for i, src in enumerate(sources):
            if src.fetch_status == "success" and src.extracted_text:
                context_parts.append(
                    f"[Source {i+1}] {src.title} ({src.url}): {src.extracted_text[:2000]}"
                )

        if not context_parts:
            return ["No sources fetched successfully"]

        context = "\n\n".join(context_parts)
        prompt = (
            "You are a research assistant. Given the user's question and the sources below, "
            "identify specific information gaps - what important aspects of the question are "
            "NOT adequately answered by the current sources?\n\n"
            f"Question: {question}\n\nSources:\n{context}\n\n"
            "List specific gaps as bullet points. Be concise. "
            'If the sources adequately answer the question, respond with "NONE".'
        )

        llm = self._get_llm()
        gaps: list[str] = []
        if llm and llm.is_available():
            try:
                response = llm.chat([
                    {"role": "system", "content": "You are a research gap analyzer. Identify what's missing."},
                    {"role": "user", "content": prompt},
                ], stream=False)
                if response:
                    for line in response.strip().split("\n"):
                        line = line.strip()
                        if line.startswith("- ") or line.startswith("* ") or line.startswith("\u2022 "):
                            gap = line[2:].strip()
                            if gap and gap.upper() != "NONE":
                                gaps.append(gap)
            except Exception as e:
                logger.warning("Gap identification failed: %s", e)
        else:
            # Fallback: simple heuristic gaps
            if len([s for s in sources if s.fetch_status == "success"]) < 2:
                gaps.append("Need more sources for verification")
            if not any(
                "date" in s.extracted_text.lower() or "202" in s.extracted_text
                for s in sources if s.fetch_status == "success"
            ):
                gaps.append("Need current/recent information")
        return gaps

    def synthesize(
        self,
        question: str,
        sources: list[ResearchSource],
        citations: list[dict[str, Any]],
    ) -> ResearchSynthesisResult:
        source_contexts = []
        for i, src in enumerate(sources):
            if src.fetch_status == "success" and src.extracted_text:
                source_contexts.append(
                    f"[Source {i+1}] {src.title}\nURL: {src.url}\nContent: {src.extracted_text[:3000]}"
                )

        if not source_contexts:
            return ResearchSynthesisResult(
                synthesis="No sources were successfully fetched and extracted.",
                confidence=0.0,
                gaps=[],
            )

        context = "\n\n---\n\n".join(source_contexts)
        citations  # accepted for API parity; not needed by the LLM prompt
        prompt = (
            "You are a research synthesizer. Using ONLY the sources below, write a comprehensive answer to the research question.\n\n"
            f"RESEARCH QUESTION: {question}\n\nSOURCES:\n{context}\n\nREQUIREMENTS:\n"
            "1. Synthesize a clear, well-structured answer\n"
            "2. Cite sources using [Source N] format inline\n"
            "3. Distinguish facts from uncertainty\n"
            "4. Note any contradictions between sources\n"
            "5. Identify remaining gaps\n"
            "6. Do NOT use information not in the sources\n"
            "7. If sources contradict, explain the contradiction\n"
            "8. Assign a confidence score 0.0-1.0 based on source quality and agreement\n\n"
            "Format your response as:\n"
            "SYNTHESIS: <your answer with [Source N] citations>\n"
            "CONFIDENCE: <0.0-1.0>\n"
            "REMAINING_GAPS: <bullet points or NONE>"
        )

        llm = self._get_llm()
        if llm and llm.is_available():
            try:
                response = llm.chat([
                    {"role": "system", "content": "You are a rigorous research synthesizer. Only use provided sources. Cite inline. Be honest about uncertainty."},
                    {"role": "user", "content": prompt},
                ], stream=False)
                if response:
                    synthesis = ""
                    confidence = 0.5
                    remaining_gaps: list[str] = []
                    for line in response.strip().split("\n"):
                        if line.startswith("SYNTHESIS:"):
                            synthesis = line[10:].strip()
                        elif line.startswith("CONFIDENCE:"):
                            try:
                                confidence = float(line[11:].strip())
                                confidence = max(0.0, min(1.0, confidence))
                            except ValueError:
                                confidence = 0.5
                        elif line.startswith("REMAINING_GAPS:"):
                            gaps_text = line[15:].strip()
                            if gaps_text.upper() != "NONE":
                                remaining_gaps = [
                                    g.strip("- *\u2022 ").strip() for g in gaps_text.split("\n") if g.strip()
                                ]
                    return ResearchSynthesisResult(
                        synthesis=synthesis or response,
                        confidence=confidence,
                        gaps=remaining_gaps,
                    )
            except Exception as e:
                logger.warning("Synthesis failed: %s", e)

        # Fallback synthesis without LLM (or on LLM error)
        return ResearchSynthesisResult(
            synthesis=self._fallback_synthesis(question, sources),
            confidence=0.3,
            gaps=[],
        )

    def _fallback_synthesis(self, query: str, sources: list[ResearchSource]) -> str:
        """Generate basic synthesis without LLM (identical to Phase 9A)."""
        successful = [s for s in sources if s.fetch_status == "success" and s.extracted_text]
        if not successful:
            return f"No sources available to answer: {query}"

        parts = [f"Research on: {query}\n\nSources consulted:"]
        for i, src in enumerate(successful):
            preview = src.extracted_text[:500].replace("\n", " ")
            parts.append(f"\n[{i+1}] {src.title} ({src.url}): {preview}...")
        parts.append(f"\n\nNote: This is a fallback synthesis. {len(successful)} source(s) were consulted.")
        parts.append("For detailed analysis with citations, an LLM provider is required.")
        return "\n".join(parts)


# =============================================================================
# Research Pipeline
# =============================================================================

class ResearchPipeline:
    """Multi-step research pipeline with safety limits and LLM synthesis."""

    def __init__(
        self,
        config: Optional[JarvisConfig] = None,
        tool_registry: Optional[ToolRegistry] = None,
        limits: Optional[ResearchLimits] = None,
        synthesizer: Optional[ResearchSynthesizer] = None,
    ) -> None:
        self.config = config or JarvisConfig()
        self.tool_registry = tool_registry
        self.limits = limits or DEFAULT_LIMITS
        self._llm_provider: Optional[LLMProvider] = None
        self._active_jobs: int = 0
        # Phase 9B DI seam: deterministic synthesizer for tests, production
        # default wraps the unchanged get_llm_provider architecture.
        self.synthesizer: ResearchSynthesizer = (
            synthesizer if synthesizer is not None else LLMResearchSynthesizer(self.config)
        )

    def _get_llm(self) -> Optional[LLMProvider]:
        """Get or create LLM provider.

        Retained for backward compatibility with existing tests that patch
        ``research.pipeline.get_llm_provider`` to None (the production
        synthesizer shares this same provider factory).
        """
        if self._llm_provider is None:
            try:
                self._llm_provider = get_llm_provider(self.config)
            except Exception as e:
                logger.warning("Failed to initialize LLM provider: %s", e)
                self._llm_provider = None
        return self._llm_provider

    def _check_concurrency(self) -> bool:
        """Check if we can start a new research job."""
        if self._active_jobs >= self.limits.max_concurrent_jobs:
            return False
        self._active_jobs += 1
        return True

    def _release_concurrency(self) -> None:
        """Release concurrency slot."""
        self._active_jobs = max(0, self._active_jobs - 1)

    def research(self, query: str, *, limits: Optional[ResearchLimits] = None) -> ResearchFindings:
        """Execute multi-step research on a query.

        Args:
            query: Research question
            limits: Optional override of default limits

        Returns:
            ResearchFindings with structured results
        """
        if not query or not query.strip():
            return ResearchFindings(query=query, completed_at=datetime.now(UTC).replace(tzinfo=None).isoformat())

        active_limits = limits or self.limits

        # Concurrency check
        if not self._check_concurrency():
            return ResearchFindings(
                query=query,
                synthesis="Research rejected: maximum concurrent jobs reached",
                confidence=0.0,
                gaps=["Concurrency limit exceeded"],
                completed_at=datetime.now(UTC).replace(tzinfo=None).isoformat()
            )

        t0 = time.perf_counter()
        findings = ResearchFindings(query=query, research_id=str(uuid.uuid4())[:8])

        try:
            # Step 1: Initial search
            findings = self._step_search(query, findings, active_limits)

            # Step 2: Fetch top results
            findings = self._step_fetch(findings, active_limits)

            # Step 3: Extract content
            findings = self._step_extract(findings, active_limits)

            # Step 4: Identify gaps
            findings = self._step_identify_gaps(findings, active_limits)

            # Step 5: Additional searches if gaps exist
            if findings.gaps and len(findings.research_steps) < active_limits.max_steps:
                findings = self._step_additional_search(findings, active_limits)
                # Re-fetch and re-extract for new sources
                findings = self._step_fetch(findings, active_limits)
                findings = self._step_extract(findings, active_limits)

            # Step 6: Synthesize
            findings = self._step_synthesize(findings, active_limits)

        except Exception as e:
            logger.exception("Research pipeline error for query: %s", query)
            findings.synthesis = f"Research failed: {e}"
            findings.confidence = 0.0
            findings.gaps.append(f"Pipeline error: {e}")

        finally:
            findings.duration_s = time.perf_counter() - t0
            findings.completed_at = datetime.now(UTC).replace(tzinfo=None).isoformat()
            self._release_concurrency()

        return findings

    # -------------------------------------------------------------------------
    # Step Implementations
    # -------------------------------------------------------------------------

    def _step_search(self, query: str, findings: ResearchFindings, limits: ResearchLimits) -> ResearchFindings:
        """Step 1: Search for relevant sources."""
        step_t0 = time.perf_counter()
        step = ResearchStep(step_number=1, action="search", query=query)

        if len(findings.research_steps) >= limits.max_searches:
            step.error = "Max searches reached"
            findings.research_steps.append(step)
            return findings

        # Use WebSearchTool if available, otherwise fallback to DDGS directly
        search_tool = None
        if self.tool_registry:
            for tool in self.tool_registry.tools:
                if tool.name == "web_search":
                    search_tool = tool
                    break

        results: list[dict[str, Any]] = []
        if search_tool:
            try:
                result = search_tool.execute(query=query)
                if result.success:
                    # Parse the summary output back into structured data
                    # This is a simplified parsing - ideally WebSearchTool would return structured data
                    import re
                    for line in result.output.split("\n"):
                        if re.match(r"^\d+\.", line.strip()):
                            # Try to extract title and URL
                            parts = line.strip().split(": ", 1)
                            if len(parts) == 2:
                                title = parts[0].split(". ", 1)[-1] if ". " in parts[0] else parts[0]
                                url_body = parts[1]
                                # Extract URL
                                url_match = re.search(r"(https?://\S+)", url_body)
                                url = url_match.group(1) if url_match else ""
                                if url:
                                    results.append({"title": title, "href": url, "body": url_body})
            except Exception as e:
                step.error = f"Search tool error: {e}"
                logger.warning("Search tool error: %s", e)

        # Fallback to DDGS (bounded) directly
        if not results and DDGS is not None:
            try:
                results = search_web(
                    query, max_results=min(5, limits.max_fetches),
                    timeout=limits.overall_timeout_s or DEFAULT_SEARCH_TIMEOUT,
                )
            except Exception as e:
                step.error = f"DDGS search error: {e}"
                logger.warning("DDGS search error: %s", e)

        step.results_count = len(results)
        step.duration_ms = (time.perf_counter() - step_t0) * 1000
        findings.research_steps.append(step)

        # Create source records
        for i, r in enumerate(results[:limits.max_fetches]):
            title = r.get("title", "").strip()
            url = r.get("href", r.get("url", "")).strip()
            if url and self._is_valid_url(url):
                source = ResearchSource(
                    title=title or url,
                    url=url,
                    search_query=query,
                    fetch_status="pending",
                    rank=i + 1,
                )
                findings.sources.append(source)

        return findings

    def _step_fetch(self, findings: ResearchFindings, limits: ResearchLimits) -> ResearchFindings:
        """Step 2: Fetch pending sources."""
        step_t0 = time.perf_counter()
        step = ResearchStep(step_number=len(findings.research_steps) + 1, action="fetch")

        fetched = 0
        total_content = 0

        for source in findings.sources:
            if source.fetch_status != "pending":
                continue
            if fetched >= limits.max_fetches:
                source.fetch_status = "skipped"
                source.fetch_error = "Max fetches reached"
                continue
            if total_content >= limits.max_total_content:
                source.fetch_status = "skipped"
                source.fetch_error = "Total content limit reached"
                continue

            try:
                fetch_result = fetch_url(source.url)
                if fetch_result.success:
                    # Truncate content if needed
                    content = fetch_result.content
                    if len(content) > limits.max_content_per_page:
                        content = content[:limits.max_content_per_page]

                    source.fetch_status = "success"
                    source.extracted_text = content  # Will be replaced by extraction
                    source.metadata = fetch_result.metadata or {}
                    fetched += 1
                    total_content += len(content)
                else:
                    source.fetch_status = "failed"
                    source.fetch_error = fetch_result.error
            except Exception as e:
                source.fetch_status = "failed"
                source.fetch_error = str(e)
                logger.warning("Fetch error for %s: %s", source.url, e)

        step.urls_fetched = fetched
        step.duration_ms = (time.perf_counter() - step_t0) * 1000
        findings.research_steps.append(step)
        return findings

    def _step_extract(self, findings: ResearchFindings, limits: ResearchLimits) -> ResearchFindings:
        """Step 3: Extract content from fetched sources."""
        step_t0 = time.perf_counter()
        step = ResearchStep(step_number=len(findings.research_steps) + 1, action="extract")

        for source in findings.sources:
            if source.fetch_status != "success":
                continue
            if not source.extracted_text:
                continue

            try:
                extraction = extract_content(source.extracted_text, url=source.url)
                if extraction.success:
                    source.extracted_text = extraction.text[:limits.max_content_per_page]
                    source.title = extraction.title or source.title
                    source.extraction_method = extraction.method
                    source.metadata.update(extraction.metadata or {})
                else:
                    # Keep raw content but mark extraction as failed
                    source.extraction_method = "none"
                    source.fetch_status = "extraction_failed"
                    source.fetch_error = extraction.error
            except Exception as e:
                source.extraction_method = "error"
                source.fetch_status = "extraction_failed"
                source.fetch_error = str(e)
                logger.warning("Extraction error for %s: %s", source.url, e)

        step.duration_ms = (time.perf_counter() - step_t0) * 1000
        findings.research_steps.append(step)
        return findings

    def _step_identify_gaps(self, findings: ResearchFindings, limits: ResearchLimits) -> ResearchFindings:
        """Step 4: Identify information gaps (via injected synthesizer)."""
        step_t0 = time.perf_counter()
        step = ResearchStep(step_number=len(findings.research_steps) + 1, action="identify_gaps")

        # No successful sources: cannot analyze gaps.
        if not any(s.fetch_status == "success" and s.extracted_text for s in findings.sources):
            step.error = "No successful sources to analyze"
            step.gaps_identified = ["No sources fetched successfully"]
            findings.gaps = step.gaps_identified
            step.duration_ms = (time.perf_counter() - step_t0) * 1000
            findings.research_steps.append(step)
            return findings

        try:
            gaps = self.synthesizer.identify_gaps(findings.query, findings.sources)
        except ResearchSynthesisError as e:
            # Graceful degradation: provider down / rate-limited.
            # Do NOT pollute findings.gaps (that drives additional searches);
            # record the failure on the step and continue.
            logger.warning("Gap identification failed (synthesizer error): %s", e)
            step.error = f"Synthesizer gap analysis failed: {e}"
            gaps = []
        except Exception as e:  # noqa: BLE001 - never let reasoning failure crash research
            logger.warning("Gap identification failed: %s", e)
            step.error = f"Gap analysis error: {e}"
            gaps = []

        step.gaps_identified = gaps
        findings.gaps = gaps
        step.duration_ms = (time.perf_counter() - step_t0) * 1000
        findings.research_steps.append(step)
        return findings

    def _step_additional_search(self, findings: ResearchFindings, limits: ResearchLimits) -> ResearchFindings:
        """Step 5: Perform additional searches for identified gaps."""
        step_t0 = time.perf_counter()
        step = ResearchStep(step_number=len(findings.research_steps) + 1, action="additional_search")

        search_tool = None
        if self.tool_registry:
            for tool in self.tool_registry.tools:
                if tool.name == "web_search":
                    search_tool = tool
                    break

        new_sources_count = 0
        existing_urls = {s.url for s in findings.sources}

        for gap in findings.gaps[:2]:  # Limit to top 2 gaps
            if len(findings.research_steps) >= limits.max_steps:
                break
            if len([s for s in findings.sources if s.fetch_status == "pending" or s.fetch_status == "success"]) >= limits.max_fetches:
                break

            # Generate search query from gap
            search_query = f"{findings.query} {gap}"
            
            results: list[dict[str, Any]] = []
            if search_tool:
                try:
                    result = search_tool.execute(query=search_query)
                    if result.success:
                        import re
                        for line in result.output.split("\n"):
                            if re.match(r"^\d+\.", line.strip()):
                                parts = line.strip().split(": ", 1)
                                if len(parts) == 2:
                                    title = parts[0].split(". ", 1)[-1] if ". " in parts[0] else parts[0]
                                    url_body = parts[1]
                                    url_match = re.search(r"(https?://\S+)", url_body)
                                    url = url_match.group(1) if url_match else ""
                                    if url and url not in existing_urls:
                                        results.append({"title": title, "href": url, "body": url_body})
                except Exception as e:
                    logger.warning("Additional search error: %s", e)

            if not results and DDGS is not None:
                try:
                    results = search_web(
                        search_query, max_results=2,
                        timeout=limits.overall_timeout_s or DEFAULT_SEARCH_TIMEOUT,
                    )
                except Exception as e:
                    logger.warning("DDGS additional search error: %s", e)

            for r in results[:2]:
                url = r.get("href", r.get("url", "")).strip()
                if url and self._is_valid_url(url) and url not in existing_urls:
                    existing_urls.add(url)
                    source = ResearchSource(
                        title=r.get("title", "").strip() or url,
                        url=url,
                        search_query=search_query,
                        fetch_status="pending",
                        rank=len(findings.sources) + 1,
                    )
                    findings.sources.append(source)
                    new_sources_count += 1

        step.results_count = new_sources_count
        step.duration_ms = (time.perf_counter() - step_t0) * 1000
        findings.research_steps.append(step)
        return findings

    def _step_synthesize(self, findings: ResearchFindings, limits: ResearchLimits) -> ResearchFindings:
        """Step 6: Synthesize findings using LLM with citations."""
        step_t0 = time.perf_counter()
        step = ResearchStep(step_number=len(findings.research_steps) + 1, action="synthesize")

        # Build context from successful sources
        source_contexts = []
        for i, src in enumerate(findings.sources):
            if src.fetch_status == "success" and src.extracted_text:
                source_contexts.append(f"[Source {i+1}] {src.title}\nURL: {src.url}\nContent: {src.extracted_text[:3000]}")

        if not source_contexts:
            step.error = "No sources available for synthesis"
            findings.synthesis = "No sources were successfully fetched and extracted."
            findings.confidence = 0.0
            step.duration_ms = (time.perf_counter() - step_t0) * 1000
            findings.research_steps.append(step)
            return findings

        citations = findings.get_citations()

        try:
            result = self.synthesizer.synthesize(findings.query, findings.sources, citations)
        except ResearchSynthesisError as e:
            # Graceful degradation: provider down / rate-limited.
            logger.warning("Synthesis failed (synthesizer error): %s", e)
            step.error = f"Synthesizer synthesis failed: {e}"
            findings.synthesis = (
                "Synthesis failed: provider unavailable or rate-limited. "
                "Raw sources are available in the findings. " + (
                    "Provider rate-limited." if e.rate_limited else str(e)
                )
            )
            findings.confidence = 0.0
            findings.gaps.append(
                "Provider rate-limited during synthesis" if e.rate_limited
                else f"Synthesis error: {e}"
            )
            findings.findings = [{
                "claim": "Synthesis unavailable (provider error)",
                "sources": citations,
                "confidence": 0.0,
            }]
            step.duration_ms = (time.perf_counter() - step_t0) * 1000
            findings.research_steps.append(step)
            return findings
        except Exception as e:  # noqa: BLE001 - never let reasoning failure crash research
            logger.warning("Synthesis failed: %s", e)
            step.error = f"Synthesis error: {e}"
            findings.synthesis = f"Synthesis failed: {e}. Raw sources available."
            findings.confidence = 0.0
            findings.findings = [{
                "claim": "Synthesis unavailable (error)",
                "sources": citations,
                "confidence": 0.0,
            }]
            step.duration_ms = (time.perf_counter() - step_t0) * 1000
            findings.research_steps.append(step)
            return findings

        findings.synthesis = result.synthesis
        findings.confidence = result.confidence
        # Preserve remaining gaps; only override when the synthesizer returned new ones.
        if result.gaps:
            findings.gaps = result.gaps
        findings.findings = [{
            "claim": "See synthesis for detailed findings",
            "sources": citations,
            "confidence": result.confidence,
        }]

        step.duration_ms = (time.perf_counter() - step_t0) * 1000
        findings.research_steps.append(step)
        return findings

    def _fallback_synthesis(self, query: str, sources: list[ResearchSource]) -> str:
        """Generate basic synthesis without LLM."""
        successful = [s for s in sources if s.fetch_status == "success" and s.extracted_text]
        if not successful:
            return f"No sources available to answer: {query}"

        parts = [f"Research on: {query}\n\nSources consulted:"]
        for i, src in enumerate(successful):
            preview = src.extracted_text[:500].replace("\n", " ")
            parts.append(f"\n[{i+1}] {src.title} ({src.url}): {preview}...")

        parts.append(f"\n\nNote: This is a fallback synthesis. {len(successful)} source(s) were consulted.")
        parts.append("For detailed analysis with citations, an LLM provider is required.")
        return "\n".join(parts)

    def _is_valid_url(self, url: str) -> bool:
        """Validate URL is HTTP/HTTPS and not blocked."""
        try:
            parsed = urlparse(url)
            return parsed.scheme in ("http", "https") and parsed.netloc != ""
        except Exception:
            return False


# =============================================================================
# Convenience Functions
# =============================================================================

def run_research(
    query: str,
    config: Optional[JarvisConfig] = None,
    tool_registry: Optional[ToolRegistry] = None,
    limits: Optional[ResearchLimits] = None,
) -> ResearchFindings:
    """Convenience function to run research pipeline."""
    pipeline = ResearchPipeline(config=config, tool_registry=tool_registry, limits=limits)
    return pipeline.research(query)


# =============================================================================
# CLI / Testing
# =============================================================================

if __name__ == "__main__":
    import sys
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "What is the capital of France?"
    print(f"Researching: {query}")
    result = run_research(query)
    print(f"\n--- Research ID: {result.research_id} ---")
    print(f"Duration: {result.duration_s:.2f}s")
    print(f"Confidence: {result.confidence}")
    print(f"Sources: {len(result.sources)}")
    print(f"Steps: {len(result.research_steps)}")
    print(f"\nSynthesis:\n{result.synthesis}")
    if result.gaps:
        print(f"\nGaps: {result.gaps}")
    for step in result.research_steps:
        print(f"  Step {step.step_number}: {step.action} ({step.duration_ms:.0f}ms)")