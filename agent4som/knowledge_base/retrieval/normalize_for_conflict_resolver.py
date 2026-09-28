from typing import Dict, List, Union

from knowledge_base.models.schemas import BaseFact, RawIndexNode


def normalize_for_conflict_resolver(
    items: List[Union[RawIndexNode, BaseFact]],
) -> List[Dict]:
    """Normalize RawIndexNode / BaseFact items to the format ConflictResolver expects.

    Each output dict has:
      - ``"tier"``:  Derived from ``RawIndexNode.source_tier``, or for ``BaseFact``
                     inferred from the fact's context.  Callers should ensure facts
                     are paired with their scope context before calling — if a fact
                     arrives without a ``source_tier``-equivalent field it defaults
                     to ``"user"`` (conservative: treat unknown facts as low-authority).
      - ``"fact"``:  ``BaseFact`` instance (wraps RawIndexNode content as a fact)
    """
    result = []
    for item in items:
        if isinstance(item, RawIndexNode):
            fact = BaseFact(
                fact_id=item.node_id,
                fact_type="raw_node",
                assistant_id="",
                program="",
                cohort_year="",
                payload={"content": item.content},
                payload_schema_version="v1",
                fact_key=item.source_file,
                source_node_ids=[item.node_id],
            )
            result.append({"tier": item.source_tier or "user", "fact": fact})
        elif isinstance(item, BaseFact):
            # BaseFact doesn't carry source_tier natively — derive from the
            # fact_key convention or default to "user" for safety.
            # A fact_key starting with "global|" or "assistant|" signals
            # authority-tier data ingested by an admin.
            tier = "user"
            if item.fact_key:
                if item.fact_key.startswith("global|"):
                    tier = "global"
                elif item.fact_key.startswith("assistant|"):
                    tier = "assistant"
            result.append({"tier": tier, "fact": item})
    return result
