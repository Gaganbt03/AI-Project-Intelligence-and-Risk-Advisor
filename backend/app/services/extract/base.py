from dataclasses import dataclass, field


@dataclass
class ExtractedSection:
    page_number: int | None = None
    section: str | None = None
    content: str = ""
    row_number: int | None = None


@dataclass
class ExtractionResult:
    text: str = ""                            # combined cleaned text
    sections: list[ExtractedSection] = field(default_factory=list)
    page_count: int | None = None
    row_count: int | None = None

    def join_sections(self) -> str:
        return "\n\n".join(s.content for s in self.sections if s.content.strip())