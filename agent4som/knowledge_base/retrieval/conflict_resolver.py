class UnresolvableSystemConflictError(Exception):
    pass

class ConflictResolver:
    def resolve(self, retrieved_items: list[dict]) -> list[dict]:
        """
        Groups retrieved items by fact_key.
        Rules:
        1. System > User. If both exist, keep System and flag it.
        2. System vs System: Keep latest effective_from. If same, raise UnresolvableSystemConflictError.
        """
        # Validate input structure before processing
        for i, item in enumerate(retrieved_items):
            if not isinstance(item, dict):
                raise TypeError(f"retrieved_items[{i}] is not a dict: {type(item)}")
            fact = item.get("fact")
            if fact is None:
                raise KeyError(f"retrieved_items[{i}] missing 'fact' key")
            if not hasattr(fact, 'fact_key'):
                raise AttributeError(f"retrieved_items[{i}].fact missing fact_key attribute")
            if "tier" not in item:
                raise KeyError(f"retrieved_items[{i}] missing 'tier' key")

        grouped = {}
        for item in retrieved_items:
            fact = item["fact"]
            if not hasattr(fact, 'payload'):
                fact.payload = {}  # tolerate missing payload for robustness
            key = fact.fact_key
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(item)

        resolved_results = []
        for items in grouped.values():
            resolved_items = self._resolve_group(items)
            resolved_results.extend(resolved_items)

        return resolved_results

    def _resolve_group(self, items: list[dict]) -> list[dict]:
        if len(items) == 1:
            return items

        system_items = [i for i in items if i["tier"] in ("system", "global", "assistant")]
        user_items = [i for i in items if i["tier"] == "user"]

        if system_items:
            # We have authority data
            if len(system_items) > 1:
                # Resolve authority conflict by effective_from
                system_items.sort(key=lambda x: x["fact"].payload.get("effective_from", 0), reverse=True)
                if system_items[0]["fact"].payload.get("effective_from", 0) == system_items[1]["fact"].payload.get("effective_from", 0):
                    raise UnresolvableSystemConflictError(f"Conflict in authoritative data for {items[0]['fact'].fact_key}")
                winner = system_items[0]
            else:
                winner = system_items[0]

            if user_items:
                # System overrides user, but we must warn
                winner["fact"].has_user_conflict_warning = True

            return [winner]

        # Only user items (ideally shouldn't have exact same fact_key in user scope, but just in case)
        return [user_items[0]]
