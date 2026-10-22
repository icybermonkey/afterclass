"""Provider-neutral AI results. Every provider returns these; the page reads them from data/lesson.json."""

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AiChapter(_Strict):
    start_card: int = Field(description="id of the card where the chapter begins")
    title: str = Field(description="short chapter title, 2-6 words")


class AiTerm(_Strict):
    term: str
    definition: str = Field(description="one or two sentences, as explained in the lecture")
    card: int = Field(description="id of the card where it is explained")


class AiCardTitle(_Strict):
    card: int
    title: str = Field(description="3-8 words: what is said or shown on this card")


class AiResult(_Strict):
    lesson_title: str
    chapters: list[AiChapter]
    glossary: list[AiTerm]
    card_titles: list[AiCardTitle] = Field(description="one quick title for every card")


class AskAnswer(_Strict):
    answer: str = Field(description="your answer in your own words, 2-6 sentences, plain text")
    main_time: float = Field(description="start time (seconds) of the single most relevant passage, -1 if none")
    sources: list[float] = Field(description="start times (seconds) of other passages the answer uses")
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
