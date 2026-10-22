"""Provider-neutral AI result. Every provider returns this; the HTML reads it from data/ai.json."""

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AiChapter(_Strict):
    start_card: int = Field(description="id of the card where the chapter begins")
    title: str = Field(description="short chapter title, 2-6 words")
    summary: str = Field(description="2-4 sentences: what is taught in this chapter")
    tags: list[str] = Field(description="3-6 lowercase keyword tags")


class AiTerm(_Strict):
    term: str
    definition: str = Field(description="one or two sentences, as explained in the lecture")
    card: int = Field(description="id of the card where it is explained")


class AiQuestion(_Strict):
    question: str
    answer: str = Field(description="a short model answer")
    card: int = Field(description="id of the card that covers the answer")


class AiKeyPoints(_Strict):
    card: int = Field(description="id of a card with a long transcript")
    points: list[str] = Field(description="2-4 short key points of what is said on this card")


class AiResult(_Strict):
    lesson_title: str
    lesson_summary: str = Field(description="4-6 sentences")
    chapters: list[AiChapter]
    glossary: list[AiTerm]
    exam_questions: list[AiQuestion]
    key_points: list[AiKeyPoints] = Field(description="for each card marked LONG")


class AiLessonSummary(_Strict):
    lesson_title: str
    lesson_summary: str = Field(description="4-6 sentences")


class AskAnswer(_Strict):
    answer: str = Field(description="the answer in Markdown-free plain text, short paragraphs")
    times: list[float] = Field(description="start times (seconds) of the passages the answer relies on")
    in_lesson: bool = Field(description="false if the lesson material does not answer the question")


def json_schema(model=None):
    """A model (default AiResult) as a self-contained JSON schema ($refs inlined, no titles)."""
    schema = (model or AiResult).model_json_schema()
    defs = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].split("/")[-1]]))
            # Drop pydantic's "title" annotations (strings), not properties that are named "title"
            return {k: walk(v) for k, v in node.items() if not (k == "title" and isinstance(v, str))}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)
