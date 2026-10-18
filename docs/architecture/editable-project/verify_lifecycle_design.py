"""Draft 0.3 reference models. NO media engine, database, provider, or full schema.

The small models exercise decisions in DETAILED_CONTRACTS.md. Tests use explicit
expected states/counterexamples; they do not certify the production application.
"""
from __future__ import annotations

import copy
import math
import unittest
from fractions import Fraction as F


class Rejected(ValueError):
    pass


def segments(rows, duration, source_duration):
    """Rows: stable id, local start/end, source start, rate, join."""
    end, previous_source, ids = F(0), None, set()
    for ident, a, b, source, rate, join in rows:
        if not ident or ident in ids or a != end or b <= a:
            raise Rejected("SEGMENT_PARTITION")
        if join not in {"cut", "continuous"}:
            raise Rejected("JOIN_POLICY")
        if join == "continuous" and source != previous_source:
            raise Rejected("DISCONTINUOUS_MAP")
        source_end = source + (b - a) * rate
        if not 0 <= source < source_duration or not 0 <= source_end <= source_duration:
            raise Rejected("SOURCE_BOUNDS")
        previous_source, end = source_end, b
        ids.add(ident)
    if not rows or end != duration:
        raise Rejected("SEGMENT_PARTITION")
    return rows


def at(rows, t):
    for ident, a, b, source, rate, join in rows:
        if a <= t < b:
            return source + (t - a) * rate
    raise Rejected("OUTSIDE_USAGE")


def inverse(rows, source_time, occurrence=None):
    result = []
    for ident, a, b, source, rate, join in rows:
        if occurrence is not None and ident != occurrence:
            continue
        if rate == 0:
            if source_time == source:
                result.append((ident, "interval", a, b))
        else:
            local = a + (source_time - source) / rate
            if a <= local < b:
                result.append((ident, "point", local))
    return result


def unique_point(rows, source_time, occurrence=None):
    solutions = inverse(rows, source_time, occurrence)
    if not solutions:
        raise Rejected("OUTSIDE_USAGE")
    if len(solutions) != 1 or solutions[0][1] != "point":
        raise Rejected("AMBIGUOUS_OCCURRENCE")
    return solutions[0][2]


def nested_at(path, t):
    for placement, rows in path:
        t = at(rows, t - placement)
    return t


def sample_span(start, end, sample_rate):
    if not 0 <= start <= end or sample_rate <= 0:
        raise Rejected("SAMPLE_RANGE")
    return math.ceil(start * sample_rate), math.ceil(end * sample_rate)


def ripple(items, cut, delta, scope):
    """Only downstream move, not a full trim/split/ripple editor."""
    moved = {i for i, c in items.items() if c["track"] in scope and c["start"] >= cut}
    for i, c in items.items():
        if c["track"] in scope and c["start"] < cut < c["start"] + c["duration"]:
            raise Rejected("CROSSING_CLIP_POLICY")
    groups = {items[i].get("sync") for i in moved} - {None}
    if any(c.get("sync") in groups and i not in moved for i, c in items.items()):
        raise Rejected("SYNC_SCOPE_REQUIRED")
    if any(items[i].get("protected") for i in moved):
        raise Rejected("PROTECTED_TARGET")
    out = copy.deepcopy(items)
    for i in moved:
        out[i]["start"] += delta
        if out[i]["start"] < 0:
            raise Rejected("NEGATIVE_PLACEMENT")
    tracks = {c["track"] for c in out.values()}
    for track in tracks:
        ordered = sorted((c for c in out.values() if c["track"] == track), key=lambda c: c["start"])
        if any(a["start"] + a["duration"] > b["start"] for a, b in zip(ordered, ordered[1:])):
            raise Rejected("COLLISION")
    return out


def handles(outgoing_end, outgoing_source_duration, incoming_start, before, after, rate=F(1)):
    if rate <= 0 or before < 0 or after < 0:
        raise Rejected("UNSUPPORTED_HANDLE_PROBE")
    if outgoing_end + after * rate > outgoing_source_duration or incoming_start - before * rate < 0:
        raise Rejected("INSUFFICIENT_HANDLES")
    return True


def affine(m, point):
    a, b, c, d, tx, ty = m
    x, y = point
    return a * x + c * y + tx, b * x + d * y + ty


def affine_inverse(m, point):
    a, b, c, d, tx, ty = m
    det = a * d - b * c
    if det == 0:
        raise Rejected("NON_INVERTIBLE_SELECTION")
    x, y = point[0] - tx, point[1] - ty
    return (d * x - c * y) / det, (-b * x + a * y) / det


def image_connection(source, target):
    for field in ("color", "alpha", "space", "pixel_aspect"):
        if source[field] != target[field]:
            raise Rejected("EXPLICIT_CONVERSION_REQUIRED:" + field)
    return True


def unpremultiply(rgb, alpha):
    return (0, 0, 0) if alpha == 0 else tuple(c / alpha for c in rgb)


def latency_delays(paths):
    """Result in rational seconds, BEFORE sample-grid conversion."""
    times = [F(samples, rate) for samples, rate in paths]
    maximum = max(times)
    return [maximum - t for t in times]


def route_matrix(in_labels, out_labels, matrix):
    if len(set(in_labels)) != len(in_labels) or len(set(out_labels)) != len(out_labels):
        raise Rejected("CHANNEL_ID")
    if len(matrix) != len(out_labels) or any(len(row) != len(in_labels) for row in matrix):
        raise Rejected("CHANNEL_MATRIX")
    if any(type(v) not in (int, float) or not math.isfinite(v) for row in matrix for v in row):
        raise Rejected("CHANNEL_MATRIX")
    return True


def adopt_groups(groups, selected):
    """Dependency closure, not creative invariants or approval authorization."""
    if not selected <= groups.keys():
        raise Rejected("MISSING_GROUP")
    for key in selected:
        if not groups[key] <= selected:
            raise Rejected("PARTIAL_DEPENDENCY")
    visiting, done = set(), set()

    def walk(key):
        if key in visiting:
            raise Rejected("GROUP_CYCLE")
        if key in done:
            return
        visiting.add(key)
        for dep in groups[key]:
            walk(dep)
        visiting.remove(key)
        done.add(key)
    for key in selected:
        walk(key)
    return selected


def replace_usages(usages, replacements):
    """Flattened full-address oracle; does not build actual DAG revisions."""
    out = dict(usages)
    for address, (expected, new) in replacements.items():
        if out.get(address) != expected:
            raise Rejected("STALE_USAGE")
        out[address] = new
    return out


def undo_fields(state, receipt):
    for field, (after_token, before_value) in receipt.items():
        if field not in state or state[field][0] != after_token:
            raise Rejected("UNDO_CONFLICT")
    out = copy.deepcopy(state)
    for field, (after_token, before_value) in receipt.items():
        out[field] = (after_token + 1, before_value)
    return out


def migrate_v1(package, choices):
    """Fixture v1 -> v2 roots, only image and AV. No production migrator."""
    if package["schema_version"] != 1:
        raise Rejected("MIGRATION_SOURCE_VERSION")
    if choices.keys() != package["commit"]["roots"].keys():
        raise Rejected("OUTPUT_DECISION_REQUIRED")
    out = copy.deepcopy(package)
    objects = {(o["id"], o["revision"]): o for o in package["objects"]}
    for name, old in package["commit"]["roots"].items():
        choice = choices[name]
        obj = objects[(old["target"]["id"], old["target"]["revision"])]
        kind = choice["profile"]["kind"]
        if kind not in {"image", "av"}:
            raise Rejected("UNSUPPORTED_MIGRATION_PROFILE")
        expected = "image" if kind == "image" else "video"
        if obj["outputs"].get(old["port"]) != expected:
            raise Rejected("MIGRATION_OUTPUT_TYPE")
        streams = {"picture": {"port": old["port"], "accepts": expected}}
        if kind == "av":
            audio = choice.get("audio_port")
            if obj["outputs"].get(audio) != "audio":
                raise Rejected("AUDIO_CHOICE_REQUIRED")
            streams["sound"] = {"port": audio, "accepts": "audio"}
        # Preserve unknown root metadata as well as opaque object payloads.
        extra = {k: copy.deepcopy(v) for k, v in old.items() if k not in {"target", "port", "accepts"}}
        out["commit"]["roots"][name] = {
            **extra,
            "target": copy.deepcopy(old["target"]), "streams": streams,
            "profile": copy.deepcopy(choice["profile"]),
        }
    out["schema_version"] = 2
    return out


def import_envelope(obj, id_map, payload_rewriter=None):
    """Never rewrite arbitrary strings in opaque payload."""
    if obj.get("payload_has_refs") and payload_rewriter is None:
        raise Rejected("REFERENCE_ADAPTER_REQUIRED")
    out = copy.deepcopy(obj)
    out["id"] = id_map[obj["id"]]
    out["inputs"] = {name: {**ref, "id": id_map[ref["id"]]} for name, ref in obj["inputs"].items()}
    if payload_rewriter:
        out["payload"] = payload_rewriter(out["payload"], id_map)
    return out


class Retention:
    """Serial event model of GC fencing; NOT a distributed lease implementation."""
    def __init__(self):
        self.epoch, self.roots, self.leases, self.state = 1, set(), set(), "live"

    def retain(self, tenant, reason, lease=False):
        if self.state != "live":
            raise Rejected("DELETING_BLOB")
        (self.leases if lease else self.roots).add((tenant, reason))
        self.epoch += 1

    def release(self, tenant, reason, lease=False):
        (self.leases if lease else self.roots).discard((tenant, reason))
        self.epoch += 1

    def mark(self):
        if self.roots or self.leases or self.state != "live":
            raise Rejected("RETAINED")
        return self.epoch

    def finalize(self, epoch):
        if epoch != self.epoch or self.roots or self.leases or self.state != "live":
            raise Rejected("GC_STALE")
        self.state = "deleting"


class Runs:
    """Serial idempotency/late result model. No external exactly-once claim."""
    def __init__(self):
        self.entries = {}

    def submit(self, tenant, operation, key, fingerprint):
        scope = (tenant, operation, key)
        if scope in self.entries:
            if self.entries[scope]["fingerprint"] != fingerprint:
                raise Rejected("IDEMPOTENCY_KEY_REUSED")
            return self.entries[scope]
        entry = {"fingerprint": fingerprint, "state": "submitting", "token": 1,
                 "candidate": None, "cancel_requested": False, "authorized": True,
                 "auto_adopt": False}
        self.entries[scope] = entry
        return entry

    @staticmethod
    def complete(entry, token, result):
        if token != entry["token"]:
            raise Rejected("STALE_WORKER")
        if not entry["authorized"]:
            raise Rejected("ACCESS_REVOKED")
        if entry["candidate"] is not None:
            if entry["candidate"] != result:
                raise Rejected("RESULT_CONFLICT")
            return entry["candidate"]
        entry["candidate"], entry["state"] = result, "succeeded"
        # Adoption is a separate authorized operation even for timely success.
        return result

    @staticmethod
    def retry(entry):
        if entry["state"] == "outcome_unknown":
            raise Rejected("RECONCILE_FIRST")
        raise Rejected("NEW_ATTEMPT_REQUIRED")


class DetailedDesignChecks(unittest.TestCase):
    def setUp(self):
        self.repeat = [("one", F(0), F(2), F(0), F(1), "cut"),
                       ("two", F(2), F(4), F(0), F(1), "cut")]
        self.items = {
            "a": {"track": "picture", "start": F(0), "duration": F(3)},
            "b": {"track": "picture", "start": F(4), "duration": F(3), "sync": "b"},
            "voice": {"track": "dialogue", "start": F(4), "duration": F(3), "sync": "b"},
        }

    def test_65_repeat_requires_occurrence_and_selects_second(self):
        segments(self.repeat, F(4), F(10))
        with self.assertRaisesRegex(Rejected, "AMBIGUOUS"):
            unique_point(self.repeat, F(1))
        self.assertEqual(unique_point(self.repeat, F(1), "two"), 3)

    def test_66_freeze_inverse_remains_interval_even_with_segment(self):
        freeze = [("hold", F(0), F(3), F(2), F(0), "cut")]
        self.assertEqual(inverse(freeze, F(2)), [("hold", "interval", 0, 3)])
        with self.assertRaisesRegex(Rejected, "AMBIGUOUS"):
            unique_point(freeze, F(2), "hold")

    def test_67_nested_half_speed_then_reverse(self):
        outer = [("slow", F(0), F(4), F(1), F(1, 2), "cut")]
        inner = [("reverse", F(0), F(5), F(8), F(-1), "cut")]
        self.assertEqual(nested_at([(F(10), outer), (F(0), inner)], F(12)), 6)

    def test_68_partition_rejects_holes_overlap_and_duplicate_ids(self):
        for new_start, new_id in ((F(3), "two"), (F(1), "two"), (F(2), "one")):
            rows = [self.repeat[0], (new_id, new_start, F(4), F(0), F(1), "cut")]
            with self.assertRaisesRegex(Rejected, "PARTITION"):
                segments(rows, F(4), F(10))

    def test_69_continuity_cannot_hide_a_jump(self):
        rows = [self.repeat[0], ("two", F(2), F(4), F(0), F(1), "continuous")]
        with self.assertRaisesRegex(Rejected, "DISCONTINUOUS"):
            segments(rows, F(4), F(10))

    def test_70_half_open_boundary_selects_new_segment(self):
        self.assertEqual(at(self.repeat, F(2)), 0)
        with self.assertRaisesRegex(Rejected, "OUTSIDE"):
            at(self.repeat, F(4))

    def test_71_reverse_cannot_start_on_nonexistent_end_sample(self):
        with self.assertRaisesRegex(Rejected, "SOURCE_BOUNDS"):
            segments([("r", F(0), F(2), F(10), F(-1), "cut")], F(2), F(10))

    def test_72_fractional_audio_bounds_quantized_only_at_destination(self):
        self.assertEqual(sample_span(F(1, 30000), F(2, 30000), 48000), (2, 4))
        self.assertEqual(sample_span(F(1, 3), F(1, 3), 48000), (16000, 16000))

    def test_73_ripple_keeps_sync_and_unrelated_gap_semantics(self):
        result = ripple(self.items, F(4), F(-1), {"picture", "dialogue"})
        self.assertEqual((result["b"]["start"], result["voice"]["start"]), (3, 3))
        self.assertEqual(self.items["b"]["start"], 4)

    def test_74_ripple_cannot_silently_leave_sync_member(self):
        with self.assertRaisesRegex(Rejected, "SYNC_SCOPE"):
            ripple(self.items, F(4), F(-1), {"picture"})

    def test_75_locked_member_rolls_back_whole_group(self):
        self.items["voice"]["protected"] = True
        before = copy.deepcopy(self.items)
        with self.assertRaisesRegex(Rejected, "PROTECTED"):
            ripple(self.items, F(4), F(-1), {"picture", "dialogue"})
        self.assertEqual(self.items, before)

    def test_76_crossing_clip_requires_policy(self):
        with self.assertRaisesRegex(Rejected, "CROSSING"):
            ripple(self.items, F(2), F(1), {"picture", "dialogue"})

    def test_77_ripple_collision_is_not_overwrite(self):
        with self.assertRaisesRegex(Rejected, "COLLISION"):
            ripple(self.items, F(4), F(-2), {"picture", "dialogue"})

    def test_78_transition_uses_both_handles_and_speed(self):
        self.assertTrue(handles(F(4), F(5), F(1), F(1, 2), F(1, 2), F(2)))
        for outgoing_length, incoming_start in ((F(9, 2), F(1)), (F(5), F(1, 2))):
            with self.assertRaisesRegex(Rejected, "INSUFFICIENT"):
                handles(F(4), outgoing_length, incoming_start, F(1, 2), F(1, 2), F(2))

    def test_79_eye_selection_round_trip(self):
        m = (F(1, 2), 0, 0, F(1, 2), 100, 20)
        self.assertEqual(affine(m, (250, 400)), (225, 220))
        self.assertEqual(affine_inverse(m, (225, 220)), (250, 400))

    def test_80_nested_spatial_transform_order_matters(self):
        child = (2, 0, 0, 2, 0, 0)
        parent = (1, 0, 0, 1, 100, 20)
        self.assertEqual(affine(parent, affine(child, (3, 4))), (106, 28))
        self.assertNotEqual(affine(child, affine(parent, (3, 4))), (106, 28))

    def test_81_singular_selection_cannot_be_guessed(self):
        with self.assertRaisesRegex(Rejected, "NON_INVERTIBLE"):
            affine_inverse((0, 0, 0, 1, 0, 0), (10, 20))

    def test_82_image_type_requires_color_alpha_space_conversions(self):
        base = {"color": "linear-srgb", "alpha": "premultiplied", "space": "source@1", "pixel_aspect": F(1)}
        self.assertTrue(image_connection(base, base))
        for field, value in (("color", "srgb"), ("alpha", "straight"), ("space", "source@2"), ("pixel_aspect", F(2))):
            with self.subTest(field=field), self.assertRaisesRegex(Rejected, "CONVERSION"):
                image_connection(base, {**base, field: value})

    def test_83_zero_alpha_is_defined_and_nonzero_unpremultiplies(self):
        self.assertEqual(unpremultiply((1, 2, 3), 0), (0, 0, 0))
        self.assertEqual(unpremultiply((F(1, 4), 0, 0), F(1, 2)), (F(1, 2), 0, 0))

    def test_84_audio_latency_compares_time_not_raw_sample_counts(self):
        self.assertEqual(latency_delays([(240, 48000), (960, 48000)]), [F(720, 48000), 0])
        self.assertEqual(latency_delays([(441, 44100), (480, 48000)]), [0, 0])

    def test_85_audio_channel_matrix_has_explicit_shape_and_finite_values(self):
        self.assertTrue(route_matrix(["M"], ["L", "R"], [[0.7], [0.7]]))
        for matrix in ([[1, 0]], [[1], [float("nan")]]):
            with self.assertRaisesRegex(Rejected, "CHANNEL_MATRIX"):
                route_matrix(["M"], ["L", "R"], matrix)

    def test_86_partial_adoption_requires_dependency_closure(self):
        groups = {"voice": set(), "lyrics": {"voice"}, "cover": set()}
        with self.assertRaisesRegex(Rejected, "PARTIAL_DEPENDENCY"):
            adopt_groups(groups, {"lyrics"})
        self.assertEqual(adopt_groups(groups, {"voice", "lyrics"}), {"voice", "lyrics"})

    def test_87_adoption_groups_cannot_form_cycle(self):
        with self.assertRaisesRegex(Rejected, "GROUP_CYCLE"):
            adopt_groups({"a": {"b"}, "b": {"a"}}, {"a", "b"})

    def test_88_same_source_can_fork_differently_at_two_usages(self):
        uses = {"main/first": "sword@1", "main/second": "sword@1", "trailer/first": "sword@1"}
        changed = replace_usages(uses, {"main/first": ("sword@1", "sword@2"), "main/second": ("sword@1", "sword@3")})
        self.assertEqual(changed, {"main/first": "sword@2", "main/second": "sword@3", "trailer/first": "sword@1"})
        self.assertEqual(set(uses.values()), {"sword@1"})

    def test_89_multi_usage_failure_has_no_partial_adoption(self):
        uses = {"one": "v1", "two": "v2"}
        with self.assertRaisesRegex(Rejected, "STALE_USAGE"):
            replace_usages(uses, {"one": ("v1", "v3"), "two": ("v1", "v3")})
        self.assertEqual(uses, {"one": "v1", "two": "v2"})

    def test_90_selective_undo_preserves_unrelated_later_edit(self):
        state = {"gain": (2, -3), "cover": (5, "new cover")}
        self.assertEqual(undo_fields(state, {"gain": (2, 0)}), {"gain": (3, 0), "cover": (5, "new cover")})

    def test_91_undo_detects_same_value_with_new_revision(self):
        state = {"gain": (4, -3), "cover": (5, "new cover")}
        with self.assertRaisesRegex(Rejected, "UNDO_CONFLICT"):
            undo_fields(state, {"gain": (2, 0)})
        self.assertEqual(state["gain"], (4, -3))

    def old_package(self):
        return {"schema_version": 1, "commit": {"roots": {"main": {"target": {"id": "seq", "revision": 1}, "port": "v", "accepts": "video", "future_label": {"keep": "yes"}}}},
                "objects": [{"id": "seq", "revision": 1, "outputs": {"v": "video", "a": "audio"}, "future": {"keep": [1, 2, 3]}}]}

    def test_92_migration_requires_explicit_output_and_audio(self):
        with self.assertRaisesRegex(Rejected, "OUTPUT_DECISION"):
            migrate_v1(self.old_package(), {})
        with self.assertRaisesRegex(Rejected, "AUDIO_CHOICE"):
            migrate_v1(self.old_package(), {"main": {"profile": {"kind": "av"}}})

    def test_93_migration_deterministic_preserves_source_and_unknown_fields(self):
        old = self.old_package()
        before = copy.deepcopy(old)
        choice = {"main": {"profile": {"kind": "av"}, "audio_port": "a"}}
        new = migrate_v1(old, choice)
        self.assertEqual(new, migrate_v1(old, choice))
        self.assertEqual(old, before)
        self.assertEqual(new["objects"], old["objects"])
        self.assertEqual(new["commit"]["roots"]["main"]["future_label"], {"keep": "yes"})
        self.assertEqual(set(new["commit"]["roots"]["main"]["streams"]), {"picture", "sound"})
        with self.assertRaisesRegex(Rejected, "SOURCE_VERSION"):
            migrate_v1(new, choice)

    def test_94_import_rewrites_only_declared_refs_and_keeps_prose(self):
        obj = {"id": "a", "inputs": {"source": {"id": "b", "revision": 1}}, "payload": {"title": "b"}, "payload_has_refs": False}
        out = import_envelope(obj, {"a": "new-a", "b": "new-b"})
        self.assertEqual(out["inputs"]["source"]["id"], "new-b")
        self.assertEqual(out["payload"]["title"], "b")

    def test_95_opaque_hidden_refs_block_remapping(self):
        obj = {"id": "a", "inputs": {}, "payload": {"future_ref": "a"}, "payload_has_refs": True}
        with self.assertRaisesRegex(Rejected, "REFERENCE_ADAPTER"):
            import_envelope(obj, {"a": "new-a"})

    def test_96_active_run_lease_prevents_gc(self):
        blob = Retention()
        blob.retain("one", "run-input", lease=True)
        with self.assertRaisesRegex(Rejected, "RETAINED"):
            blob.mark()
        blob.release("one", "run-input", lease=True)
        blob.finalize(blob.mark())
        self.assertEqual(blob.state, "deleting")

    def test_97_other_tenant_retention_survives_first_tenant_delete(self):
        blob = Retention()
        blob.retain("one", "adopted")
        blob.retain("two", "adopted")
        blob.release("one", "adopted")
        with self.assertRaisesRegex(Rejected, "RETAINED"):
            blob.mark()

    def test_98_new_lease_invalidates_old_gc_mark(self):
        blob = Retention()
        mark = blob.mark()
        blob.retain("one", "export", lease=True)
        with self.assertRaisesRegex(Rejected, "GC_STALE"):
            blob.finalize(mark)

    def test_99_deleting_blob_cannot_acquire_new_reference(self):
        blob = Retention()
        blob.finalize(blob.mark())
        with self.assertRaisesRegex(Rejected, "DELETING_BLOB"):
            blob.retain("one", "adopted")

    def test_100_idempotency_scoped_and_payload_bound(self):
        runs = Runs()
        first = runs.submit("one", "generate", "key", "fp")
        self.assertIs(first, runs.submit("one", "generate", "key", "fp"))
        self.assertIsNot(first, runs.submit("two", "generate", "key", "fp"))
        with self.assertRaisesRegex(Rejected, "KEY_REUSED"):
            runs.submit("one", "generate", "key", "different")

    def test_101_unknown_provider_outcome_cannot_blind_retry(self):
        entry = Runs().submit("one", "generate", "key", "fp")
        entry["state"] = "outcome_unknown"
        with self.assertRaisesRegex(Rejected, "RECONCILE_FIRST"):
            Runs.retry(entry)

    def test_102_late_completion_retains_candidate_without_adopting(self):
        entry = Runs().submit("one", "generate", "key", "fp")
        entry.update(state="cancel_requested", cancel_requested=True)
        Runs.complete(entry, 1, "result@1")
        self.assertEqual(entry["candidate"], "result@1")
        self.assertTrue(entry["cancel_requested"])
        self.assertFalse(entry["auto_adopt"])

    def test_103_duplicate_completion_has_single_result_and_conflicts_diagnosed(self):
        entry = Runs().submit("one", "generate", "key", "fp")
        self.assertEqual(Runs.complete(entry, 1, "result@1"), Runs.complete(entry, 1, "result@1"))
        with self.assertRaisesRegex(Rejected, "RESULT_CONFLICT"):
            Runs.complete(entry, 1, "result@2")

    def test_104_old_worker_fenced_after_takeover(self):
        entry = Runs().submit("one", "generate", "key", "fp")
        entry["token"] = 2
        with self.assertRaisesRegex(Rejected, "STALE_WORKER"):
            Runs.complete(entry, 1, "result@1")
        self.assertIsNone(entry["candidate"])

    def test_105_authorization_rechecked_at_publication(self):
        entry = Runs().submit("one", "generate", "key", "fp")
        entry["authorized"] = False
        with self.assertRaisesRegex(Rejected, "ACCESS_REVOKED"):
            Runs.complete(entry, 1, "result@1")
        self.assertIsNone(entry["candidate"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
