"""Compare fixed dependency closures, without invalidating historical artifacts."""

from app.modules.engineering import documents, instances


def compare(db, work_id, before, after, derivations=()):
    def graph(ref):
        rows = documents.closure(db, work_id, ref) if ref else []
        return {(r["id"], r["revision"]): r for r in rows}

    old, new = graph(before), graph(after)
    old_ids = {key[0] for key in old}
    new_ids = {key[0] for key in new}
    old_versions = {}
    new_versions = {}
    for identity, revision in old:
        old_versions.setdefault(identity, set()).add(revision)
    for identity, revision in new:
        new_versions.setdefault(identity, set()).add(revision)
    changed = {i for i in old_ids & new_ids if old_versions[i] != new_versions[i]}
    added, removed = new_ids - old_ids, old_ids - new_ids

    def outputs(rows, reference):
        if not reference:
            return {}
        return rows[(reference["id"], reference["revision"])]["payload"]["outputs"]

    old_outputs, new_outputs = outputs(old, before), outputs(new, after)

    def output_ref(rows, root, output):
        manifest = rows[(root["id"], root["revision"])]
        return manifest["inputs"][output["input"]]["target"]

    affected = []
    for name in sorted(old_outputs.keys() | new_outputs.keys()):
        if name not in old_outputs:
            reason = "added"
        elif name not in new_outputs:
            reason = "removed"
        elif old_outputs[name] != new_outputs[name] or output_ref(
            old, before, old_outputs[name]
        ) != output_ref(new, after, new_outputs[name]):
            reason = "root_revision_changed"
        else:
            continue
        affected.append({"output": name, "reason": reason})
    usage_changes = []
    unresolved = []
    for identity in sorted(changed):
        # Several revisions may coexist intentionally; never invent their pairing.
        if len(old_versions[identity]) != 1 or len(new_versions[identity]) != 1:
            unresolved.append(identity)
            continue
        previous = instances.references(old[(identity, next(iter(old_versions[identity])))])
        current = instances.references(new[(identity, next(iter(new_versions[identity])))])
        if previous is None or current is None:
            continue
        names = instances.labels(old[(identity, next(iter(old_versions[identity])))])
        names.update(instances.labels(new[(identity, next(iter(new_versions[identity])))]))
        for instance_id, slot in sorted(previous.keys() | current.keys()):
            key = (instance_id, slot)
            if previous.get(key) != current.get(key):
                usage_changes.append(
                    {
                        "document_id": identity,
                        "instance_id": instance_id,
                        "instance_name": names.get(instance_id, "使用位置"),
                        "slot": slot,
                        "before": previous.get(key),
                        "after": current.get(key),
                    }
                )
    derived = {item["document_id"]: item["derived_from"] for item in derivations}
    paths = []
    visits = 0
    truncated = False

    def trace(output, previous_ref, current_ref, path):
        nonlocal visits, truncated
        if previous_ref == current_ref:
            return
        visits += 1
        if visits > 4096:
            truncated = True
            return
        if (
            previous_ref["id"] != current_ref["id"]
            and derived.get(current_ref["id"]) != previous_ref
        ):
            return  # A replacement is not proof of shared instance identity.
        old_node = old[(previous_ref["id"], previous_ref["revision"])]
        new_node = new[(current_ref["id"], current_ref["revision"])]
        previous, current = instances.references(old_node), instances.references(new_node)
        if previous is None or current is None:
            return
        names = {**instances.labels(old_node), **instances.labels(new_node)}
        for key in sorted(previous.keys() | current.keys()):
            if len(paths) >= 4096:
                truncated = True
                return
            left, right = previous.get(key), current.get(key)
            if left == right:
                continue
            next_path = [
                *path,
                {"instance_id": key[0], "name": names.get(key[0], "使用位置"), "slot": key[1]},
            ]
            paths.append({"output": output, "path": next_path, "before": left, "after": right})
            if left and right:
                trace(output, left, right, next_path)

    for name in sorted(old_outputs.keys() & new_outputs.keys()):
        trace(
            name,
            output_ref(old, before, old_outputs[name]),
            output_ref(new, after, new_outputs[name]),
            [],
        )
    from app.modules.engineering import temporal_impact

    temporal = [
        {
            "output": name,
            **temporal_impact.compare(
                old,
                new,
                output_ref(old, before, old_outputs[name]),
                output_ref(new, after, new_outputs[name]),
                derivations,
            ),
        }
        for name in sorted(old_outputs.keys() & new_outputs.keys())
        if output_ref(old, before, old_outputs[name]) != output_ref(new, after, new_outputs[name])
    ]

    from app.modules.engineering.native_v2 import review_items

    return {
        "added_documents": sorted(added),
        "removed_documents": sorted(removed),
        "changed_documents": sorted(changed),
        "affected_outputs": affected,
        "instance_input_changes": usage_changes,
        "ambiguous_revision_documents": unresolved,
        "dependency_paths": paths,
        "dependency_paths_truncated": truncated,
        "sync_reviews": review_items(new.values()),
        "temporal_impacts": temporal,
        "historical_artifacts_invalidated": False,
        "note": "固定引用没有升级的其他输出保持原版本；本报告不代表视觉质量评审。",
    }
