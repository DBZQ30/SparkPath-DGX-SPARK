"""Context stitching — expand retrieval hits with adjacent nodes.

When a search hit lands in the middle of a document section, the
``prev_node_id`` / ``next_node_id`` chain is followed to pull in one
adjacent node on each side, preventing fragmented answers.

``build_passages`` is the recommended entry point: it expands each hit
by ±1 window, merges overlapping windows from the same document, and
returns coherent passages instead of fragmented chunks. This follows
the industry-standard "Sentence Window / Parent Document Retrieval"
pattern: retrieve at chunk level, return at passage level.
"""

from __future__ import annotations

import copy

from knowledge_base.models.schemas import RawIndexNode
from knowledge_base.repository.interfaces import KnowledgeBaseRepository

# ── Passage builder (recommended) ────────────────────────────────────────


def build_passages(
    hits: list[RawIndexNode],
    all_nodes: list[RawIndexNode],
    window: int = 1,
) -> list[RawIndexNode]:
    """Expand *hits* by ±*window* neighbours, merge overlapping windows.

    Industry standard pattern: retrieve at chunk level, return at passage
    level.  Adjacent chunks from the same document are merged into
    coherent passages so the LLM sees complete paragraphs instead of
    fragmented snippets.

    Args:
        hits: Reranked top-N nodes (ordered best → worst).
        all_nodes: Full search pool for neighbour lookup.
        window: Neighbours to pull in each direction (default ±1).

    Returns:
        A list of ``RawIndexNode`` where each node's ``content`` is a
        merged passage.  Metadata (source_file, section_title, etc.)
        inherits from the highest-ranked hit in that passage.
    """
    if not hits:
        return []

    node_map = {n.node_id: n for n in all_nodes}
    # Build source_file → sorted chunk list for neighbour traversal
    source_chains: dict[str, list[str]] = {}  # source_file → [node_id, ...]
    for n in all_nodes:
        if n.source_file:
            source_chains.setdefault(n.source_file, []).append(n.node_id)

    # For each hit, expand to a window of node_ids using prev/next links
    # (or source_file adjacency as fallback)
    windows: list[tuple[str, list[str]]] = []  # [(source_file, [node_ids])]
    for hit in hits:
        sf = hit.source_file or ""
        chain_ids = source_chains.get(sf, [])

        # Build contiguous range from prev/next links
        ids: set[str] = {hit.node_id}
        _expand(hit, node_map, chain_ids, ids, window)
        _expand(hit, node_map, chain_ids, ids, window, forward=False)

        # Sort ids by their position in the document chain
        if chain_ids:
            ordered = sorted(ids, key=lambda nid: _index_in_chain(nid, chain_ids))
        else:
            ordered = list(ids)
        windows.append((sf, ordered))

    # Merge overlapping windows from the same document
    passages = _merge_windows(windows, hits, node_map)

    # Build passage nodes: join chunk contents, inherit metadata from best hit
    result: list[RawIndexNode] = []
    for _sf, node_ids, best_hit in passages:
        chunks = [node_map[nid].content for nid in node_ids if nid in node_map]
        passage_node = copy.copy(best_hit)
        passage_node.content = "\n\n".join(chunks)
        result.append(passage_node)

    return result


def _expand(
    node: RawIndexNode,
    node_map: dict[str, RawIndexNode],
    chain_ids: list[str],
    collected: set[str],
    window: int,
    forward: bool = True,
) -> None:
    """Walk *window* steps along prev/next links, collecting node_ids."""
    current = node
    for _ in range(window):
        nid = current.next_node_id if forward else current.prev_node_id
        if not nid or nid not in node_map:
            # Fallback: use chain position
            idx = _index_in_chain(current.node_id, chain_ids)
            if idx < 0:
                break
            target_idx = idx + 1 if forward else idx - 1
            if 0 <= target_idx < len(chain_ids):
                nid = chain_ids[target_idx]
            else:
                break
        if nid in collected:
            break
        collected.add(nid)
        current = node_map[nid]


def _index_in_chain(node_id: str, chain_ids: list[str]) -> int:
    try:
        return chain_ids.index(node_id)
    except ValueError:
        return -1


def _merge_windows(
    windows: list[tuple[str, list[str]]],
    hits: list[RawIndexNode],
    node_map: dict[str, RawIndexNode],
) -> list[tuple[str, list[str], RawIndexNode]]:
    """Merge overlapping windows from the same source_file.

    Returns list of (source_file, merged_node_ids, best_hit).
    """
    # Build hit rank map: lower index = better
    rank = {n.node_id: i for i, n in enumerate(hits)}

    # Group windows by source_file, merge overlapping
    merged: list[tuple[str, list[str], RawIndexNode]] = []
    for sf, node_ids in windows:
        # Find if this overlaps with any existing passage
        overlap_idx = -1
        for i, (msf, mids, _) in enumerate(merged):
            if msf == sf and set(node_ids) & set(mids):
                overlap_idx = i
                break
        if overlap_idx >= 0:
            # Merge
            merged_ids = list(dict.fromkeys(merged[overlap_idx][1] + node_ids))
            old_best = merged[overlap_idx][2]
            new_best = old_best if rank.get(old_best.node_id, 999) <= rank.get(node_ids[0] if node_ids else "", 999) else node_map.get(node_ids[0], old_best) if node_ids else old_best
            merged[overlap_idx] = (sf, merged_ids, new_best)
        else:
            best = min(
                (node_map[nid] for nid in node_ids if nid in node_map),
                key=lambda n: rank.get(n.node_id, 999),
                default=hits[0],
            )
            merged.append((sf, node_ids, best))

    # Sort passages by best hit rank (preserve reranker order)
    merged.sort(key=lambda m: rank.get(m[2].node_id, 999))
    return merged


# ── Legacy stitching (deprecated, use build_passages instead) ─────────


def stitch_adjacent_nodes(
    repo: KnowledgeBaseRepository,
    hits: list[RawIndexNode],
    scope: str,
    window: int = 1,
) -> list[RawIndexNode]:
    """Expand *hits* with adjacent nodes from the same document.

    For each hit with a non-empty ``prev_node_id`` / ``next_node_id``,
    fetch up to *window* neighbour nodes.  The original *hits* always
    appear first in the result, followed by deduplicated adjacents.

    Args:
        repo: The repository to query for adjacent nodes.
        hits: Original retrieval results.
        scope: The scope these nodes belong to.
        window: How many neighbours to fetch in each direction.

    Returns:
        Expanded list with originals first, then adjacents.
    """
    if not hits:
        return []

    # Collect candidate ids
    neighbour_ids: set[str] = set()
    for node in hits:
        if node.prev_node_id:
            neighbour_ids.add(node.prev_node_id)
        if node.next_node_id:
            neighbour_ids.add(node.next_node_id)

    # Don't re-fetch nodes already in hits
    hit_ids = {n.node_id for n in hits}
    neighbour_ids -= hit_ids

    if not neighbour_ids:
        return list(hits)

    # ChromaDB node_id lookup is not yet implemented — use build_passages()
    # which does in-process expansion from the already-fetched node set.
    raise NotImplementedError(
        "stitch_adjacent_nodes is deprecated — use build_passages() "
        "which expands hits in-process without additional DB queries."
    )


def _adjacent_nodes_for_hit(
    hit: RawIndexNode,
    node_map: dict[str, RawIndexNode],
    source_groups: dict[str, list[RawIndexNode]],
) -> list[RawIndexNode]:
    """单个命中的邻接节点（按输出顺序）：prev/next 链接优先，
    无链接时按 source_file 兄弟列表回退（前后各一）。"""
    neighbours: list[RawIndexNode] = []

    if hit.prev_node_id and hit.prev_node_id in node_map:
        neighbours.append(node_map[hit.prev_node_id])
    if hit.next_node_id and hit.next_node_id in node_map:
        neighbours.append(node_map[hit.next_node_id])
    if hit.prev_node_id or hit.next_node_id:
        return neighbours

    # Fallback: no prev/next links — find hit's position in the sibling list
    siblings = source_groups.get(hit.source_file or "", [])
    try:
        pos = next(i for i, s in enumerate(siblings) if s.node_id == hit.node_id)
    except StopIteration:
        return neighbours
    if pos > 0:
        neighbours.append(siblings[pos - 1])
    if pos < len(siblings) - 1:
        neighbours.append(siblings[pos + 1])
    return neighbours


def enrich_hits_with_adjacent(
    hits: list[RawIndexNode],
    all_nodes: list[RawIndexNode],
    window: int = 1,
) -> list[RawIndexNode]:
    """In-process enrichment: when *all_nodes* is already available, stitch
    adjacent nodes from the same list without additional DB queries.

    This is useful when the caller has already fetched a superset of nodes
    and wants to expand each hit with its immediate neighbours from the
    original document structure.
    """
    node_map = {n.node_id: n for n in all_nodes}
    # Build source_file → sorted-node list for prev/next fallback
    source_groups: dict[str, list[RawIndexNode]] = {}
    for n in all_nodes:
        if n.source_file:
            source_groups.setdefault(n.source_file, []).append(n)

    result: list[RawIndexNode] = []
    seen: set[str] = set()

    for hit in hits:
        for node in (hit, *_adjacent_nodes_for_hit(hit, node_map, source_groups)):
            if node.node_id not in seen:
                result.append(node)
                seen.add(node.node_id)

    return result
