from __future__ import annotations

from pydantic import BaseModel, Field


class SubQuestion(BaseModel):
    id: str = Field(description="Unique identifier for this sub-question")
    question: str = Field(description="The research sub-question to investigate")
    search_queries: list[str] = Field(
        description="1-3 search queries to answer this sub-question",
    )
    priority: int = Field(default=1, description="Priority rank (1=highest)")
    rationale: str = Field(description="Why this sub-question matters for the overall topic")


class Plan(BaseModel):
    sub_questions: list[SubQuestion] = Field(
        description="List of 3-8 sub-questions to investigate",
    )
    overall_approach: str = Field(description="Brief description of the research approach")


class SourceRef(BaseModel):
    url: str = Field(default="", description="URL of the source")
    title: str = Field(default="", description="Title of the source")


class SubReport(BaseModel):
    sub_question_id: str = Field(description="ID of the sub-question this report answers")
    question: str = Field(description="The original sub-question text")
    findings: str = Field(description="300-500 word summary of findings")
    key_facts: list[str] = Field(
        description="3-5 key facts discovered",
    )
    confidence: float = Field(
        description="Confidence score 0-1, where 1 is fully supported by evidence",
    )
    gaps: str = Field(description="Description of information gaps or limitations")
    sources: list[SourceRef] = Field(default_factory=list, description="Sources used")


class ReportSection(BaseModel):
    heading: str = Field(description="Section heading")
    content: str = Field(description="Section content in markdown")


class ResearchReport(BaseModel):
    title: str = Field(description="Research report title")
    executive_summary: str = Field(description="2-3 paragraph executive summary")
    sections: list[ReportSection] = Field(description="Report body sections")
    key_findings: list[str] = Field(description="5-10 key findings as bullet points")
    limitations: str = Field(description="Limitations and caveats of this research")
    sources: list[SourceRef] = Field(default_factory=list, description="Deduplicated source list")
