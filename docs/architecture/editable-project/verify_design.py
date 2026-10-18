"""Executable design probes, not a production engine or full schema validator.

Run with Python 3.10+ from any directory. Uses only the standard library.
Fixtures have no real media; passing does not verify rendering or storage.
"""
from __future__ import annotations

import copy
import json
import re
import unittest
from fractions import Fraction
from pathlib import Path


FIXTURE = Path(__file__).with_name("example.json")
KNOWN_OPERATORS = {
    "studio.trail@1", "studio.glow@1", "studio.occluded_over@1", "studio.lyrics@1"
}
KNOWN_KINDS = {"resource", "analysis", "effect", "composition", "instance", "sequence", "image_document"}


class Invalid(ValueError):
    pass


def time_value(value):
    if not isinstance(value, dict) or set(value) != {"n", "d"}:
        raise Invalid("TIME_SHAPE")
    if not all(isinstance(value[k], str) and re.fullmatch(r"-?(0|[1-9][0-9]*)", value[k]) for k in value):
        raise Invalid("TIME_INTEGER_STRING")
    if int(value["d"]) <= 0:
        raise Invalid("TIME_DENOMINATOR")
    return Fraction(int(value["n"]), int(value["d"]))


def key(ref):
    if (not isinstance(ref, dict) or not isinstance(ref.get("id"), str)
            or not ref["id"] or type(ref.get("revision")) is not int or ref["revision"] < 1):
        raise Invalid("FIXED_REVISION_REQUIRED")
    return ref["id"], ref["revision"]


def index(package):
    result = {}
    for obj in package["objects"]:
        k = key(obj)
        if k in result:
            raise Invalid("DUPLICATE_REVISION")
        result[k] = obj
    return result


def check_ref(ref, objects):
    target = key(ref["target"])
    if target not in objects:
        raise Invalid("MISSING_REFERENCE")
    output = objects[target]["outputs"].get(ref["port"])
    if output is None:
        raise Invalid("MISSING_PORT")
    if output != ref["accepts"]:
        raise Invalid("PORT_MISMATCH")
    return target


def duration(obj):
    if obj["kind"] not in KNOWN_KINDS or obj["schema_version"] != 1:
        return None
    payload = obj["payload"]
    if obj["kind"] == "instance":
        return time_value(payload["placement"]["duration"])
    return time_value(payload["duration"]) if "duration" in payload else None


def validate(package):
    if package["format"] != "studio.editable-project" or package["schema_version"] != 2:
        raise Invalid("UNSUPPORTED_FORMAT")
    objects = index(package)
    for root in package["commit"]["roots"].values():
        for stream in root["streams"].values():
            check_ref({"target": root["target"], **stream}, objects)
    for obj in objects.values():
        for ref in obj["inputs"].values():
            check_ref(ref, objects)
        if obj["kind"] in KNOWN_KINDS and obj["schema_version"] == 1 and duration(obj) is not None and duration(obj) <= 0:
            raise Invalid("INVALID_DURATION")
    visiting, visited = set(), set()

    def visit(k):
        if k in visiting:
            raise Invalid("DEPENDENCY_CYCLE")
        if k in visited:
            return
        visiting.add(k)
        for ref in objects[k]["inputs"].values():
            visit(key(ref["target"]))
        visiting.remove(k)
        visited.add(k)

    for k in objects:
        visit(k)
    for obj in objects.values():
        if obj["kind"] not in KNOWN_KINDS or obj["schema_version"] != 1:
            continue
        payload = obj["payload"]
        if obj["kind"] == "instance":
            if payload["bounds_policy"] != "error" or payload["source_mapping"]["mode"] != "linear":
                raise Invalid("UNSUPPORTED_PROBE_MAPPING")
            start = time_value(payload["placement"]["start"])
            if start < 0:
                raise Invalid("NEGATIVE_PLACEMENT")
            mapping = payload["source_mapping"]
            source_start = time_value(mapping["source_start"])
            rate = time_value(mapping["rate"])
            source_end = source_start + duration(obj) * rate
            source = objects[key(obj["inputs"]["source"]["target"])]
            if source["kind"] not in KNOWN_KINDS or source["schema_version"] != 1:
                # Preserve opaque sources; execution preflight must block them.
                continue
            source_duration = duration(source)
            if source_duration is None:
                raise Invalid("EXPLICIT_HOLD_REQUIRED")
            if not 0 <= source_start < source_duration or not 0 <= source_end <= source_duration:
                raise Invalid("SOURCE_BOUNDS")
        if obj["kind"] == "sequence":
            names = []
            for track in payload["tracks"]:
                for name in track["items"]:
                    names.append(name)
                    if name not in obj["inputs"]:
                        raise Invalid("MISSING_TRACK_INPUT")
                    ref = obj["inputs"][name]
                    if ref["accepts"] != track["kind"]:
                        raise Invalid("TRACK_KIND")
                    child = objects[key(ref["target"])]
                    if child["kind"] != "instance":
                        raise Invalid("PLACEMENT_REQUIRED")
                    end = time_value(child["payload"]["placement"]["start"]) + duration(child)
                    if end > duration(obj):
                        raise Invalid("PARENT_BOUNDS")
            if len(names) != len(set(names)) or set(names) != set(obj["inputs"]):
                raise Invalid("TRACK_MEMBERSHIP")
    return objects


def closure(package, root="main"):
    objects = index(package)
    seen = set()

    def walk(k):
        if k not in seen:
            seen.add(k)
            for ref in objects[k]["inputs"].values():
                walk(key(ref["target"]))

    walk(key(package["commit"]["roots"][root]["target"]))
    return seen


def unsupported(package, supported, root="main"):
    objects = validate(package)
    return {
        objects[k]["payload"]["operator"] for k in closure(package, root)
        if "operator" in objects[k]["payload"] and objects[k]["payload"]["operator"] not in supported
    }


def affected_objects(package, changed):
    """Potential upgrade impact, not invalidation of immutable old-version caches."""
    objects = index(package)
    affected = {changed}
    while True:
        more = {k for k, obj in objects.items() if any(key(ref["target"]) in affected for ref in obj["inputs"].values())}
        if more <= affected:
            return affected
        affected |= more


def edit_usage(package, root, path, edit):
    """Copy-on-write selected reference path, retaining all old revisions."""
    result = copy.deepcopy(package)
    objects = index(result)

    def rewrite(k, remaining):
        obj = copy.deepcopy(objects[k])
        if remaining:
            ref = obj["inputs"][remaining[0]]
            ref["target"] = rewrite(key(ref["target"]), remaining[1:])
        else:
            edit(obj)
        obj["revision"] = max(v for ident, v in objects if ident == obj["id"]) + 1
        objects[key(obj)] = obj
        result["objects"].append(obj)
        return {"id": obj["id"], "revision": obj["revision"]}

    ref = result["commit"]["roots"][root]
    ref["target"] = rewrite(key(ref["target"]), path)
    result["commit"]["id"] = "candidate-commit"
    validate(result)
    return result


def submit_scalar_probe(state, expected_reads, writes, invariant=lambda _: True):
    """Single-process model of preconditions and atomic staging, NOT DB concurrency."""
    if any(k not in state or state[k] != v for k, v in expected_reads.items()):
        raise Invalid("STALE_READ")
    if not set(writes) <= set(expected_reads):
        raise Invalid("WRITE_PRECONDITION_REQUIRED")
    candidate = copy.deepcopy(state)
    candidate.update(writes)
    if not invariant(candidate):
        raise Invalid("CONSTRAINT_VIOLATION")
    return candidate


class DesignChecks(unittest.TestCase):
    def setUp(self):
        self.p = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def obj(self, name, revision=1):
        return index(self.p)[name, revision]

    def test_01_fixture_closure_and_types(self):
        self.assertEqual(len(validate(self.p)), 17)
        self.assertEqual(unsupported(self.p, KNOWN_OPERATORS), set())

    def test_02_missing_reference_rejected(self):
        self.obj("glow")["inputs"]["image"]["target"]["revision"] = 99
        with self.assertRaisesRegex(Invalid, "MISSING_REFERENCE"):
            validate(self.p)

    def test_03_audio_cannot_become_visual_without_adapter(self):
        self.obj("glow")["inputs"]["image"] = {"target": {"id": "music", "revision": 1}, "port": "audio", "accepts": "video"}
        with self.assertRaisesRegex(Invalid, "PORT_MISMATCH"):
            validate(self.p)

    def test_04_cycle_rejected(self):
        self.obj("glow")["inputs"]["image"]["target"] = {"id": "sword-comp", "revision": 1}
        self.obj("glow")["inputs"]["image"]["port"] = "video"
        with self.assertRaisesRegex(Invalid, "DEPENDENCY_CYCLE"):
            validate(self.p)

    def test_05_latest_not_allowed(self):
        self.obj("glow")["inputs"]["image"]["target"]["revision"] = "latest"
        with self.assertRaisesRegex(Invalid, "FIXED_REVISION_REQUIRED"):
            validate(self.p)

    def test_06_duplicate_revision_rejected(self):
        self.p["objects"].append(copy.deepcopy(self.obj("voice")))
        with self.assertRaisesRegex(Invalid, "DUPLICATE_REVISION"):
            validate(self.p)

    def test_07_source_range_rejected(self):
        self.obj("fight-a")["payload"]["source_mapping"]["source_start"] = {"n": "9", "d": "1"}
        with self.assertRaisesRegex(Invalid, "SOURCE_BOUNDS"):
            validate(self.p)

    def test_08_parent_range_rejected(self):
        self.obj("fight-b")["payload"]["placement"]["start"] = {"n": "6", "d": "1"}
        with self.assertRaisesRegex(Invalid, "PARENT_BOUNDS"):
            validate(self.p)

    def test_09_time_denominator_rejected(self):
        with self.assertRaisesRegex(Invalid, "TIME_DENOMINATOR"):
            time_value({"n": "1", "d": "0"})

    def test_10_fractional_frame_rate_has_no_rounding_accumulation(self):
        frame = time_value({"n": "1001", "d": "30000"})
        self.assertEqual(frame * 30000, 1001)

    def test_11_half_speed_range(self):
        self.obj("fight-a")["payload"]["source_mapping"]["rate"] = {"n": "1", "d": "2"}
        validate(self.p)
        self.assertEqual(Fraction(2) + Fraction(4) * Fraction(1, 2), 4)

    def test_12_reverse_range(self):
        m = self.obj("fight-a")["payload"]["source_mapping"]
        m["source_start"], m["rate"] = {"n": "6", "d": "1"}, {"n": "-1", "d": "1"}
        validate(self.p)

    def test_13_freeze_valid_video_sample(self):
        self.obj("fight-a")["payload"]["source_mapping"]["rate"] = {"n": "0", "d": "1"}
        validate(self.p)

    def test_14_edit_one_usage_preserves_second(self):
        before = copy.deepcopy(self.p)
        new = edit_usage(self.p, "main", ["first", "source", "glow"], lambda o: o["payload"]["parameters"]["intensity"].update(value=0.3))
        objects = index(new)
        first = objects[key(objects["sequence", 2]["inputs"]["first"]["target"])]
        second = objects[key(objects["sequence", 2]["inputs"]["second"]["target"])]
        self.assertEqual(first["inputs"]["source"]["target"]["revision"], 2)
        self.assertEqual(second["inputs"]["source"]["target"]["revision"], 1)
        self.assertEqual(objects["glow", 1]["payload"]["parameters"]["intensity"]["value"], 0.7)
        self.assertEqual(objects["glow", 2]["payload"]["parameters"]["intensity"]["value"], 0.3)
        self.assertEqual(self.p, before)

    def test_15_font_change_affects_lyrics_and_poster_not_sword(self):
        affected = affected_objects(self.p, ("font", 1))
        self.assertTrue({("lyrics", 1), ("poster", 1), ("sequence", 1)} <= affected)
        self.assertNotIn(("sword-comp", 1), affected)

    def test_16_tracking_change_affects_both_usages(self):
        affected = affected_objects(self.p, ("tracking", 1))
        self.assertTrue({("fight-a", 1), ("fight-b", 1), ("sequence", 1)} <= affected)
        self.assertNotIn(("music-use", 1), affected)

    def test_17_unknown_operator_is_preserved_but_not_renderable(self):
        self.obj("glow")["payload"]["operator"] = "future.volumetric@7"
        self.obj("glow")["payload"]["opaque_data"] = {"keep": [1, 2, 3]}
        saved = json.loads(json.dumps(self.p))
        self.assertEqual(saved, self.p)
        self.assertEqual(unsupported(saved, KNOWN_OPERATORS), {"future.volumetric@7"})

    def test_18_unrelated_output_can_preflight(self):
        self.obj("glow")["payload"]["operator"] = "future.volumetric@7"
        self.assertEqual(unsupported(self.p, KNOWN_OPERATORS, "poster"), set())

    def test_19_no_global_script_requirement(self):
        poster = closure(self.p, "poster")
        self.p["objects"] = [o for o in self.p["objects"] if key(o) in poster]
        del self.p["commit"]["roots"]["main"]
        validate(self.p)

    def test_20_deleted_source_is_diagnosed(self):
        self.p["objects"] = [o for o in self.p["objects"] if o["id"] != "music"]
        with self.assertRaisesRegex(Invalid, "MISSING_REFERENCE"):
            validate(self.p)

    def test_21_stale_dialogue_invalidates_subtitle_write(self):
        state = {"dialogue.duration": 5, "subtitle.revision": 1}
        with self.assertRaisesRegex(Invalid, "STALE_READ"):
            submit_scalar_probe(state, {"dialogue.duration": 4, "subtitle.revision": 1}, {"subtitle.revision": 2})

    def test_22_disjoint_change_preserves_new_head(self):
        state = {"dialogue.revision": 2, "poster.revision": 1}
        result = submit_scalar_probe(state, {"poster.revision": 1}, {"poster.revision": 2})
        self.assertEqual(result, {"dialogue.revision": 2, "poster.revision": 2})

    def test_23_failed_group_does_not_partially_mutate(self):
        state = {"audio.end": 4, "subtitle.end": 4}
        with self.assertRaisesRegex(Invalid, "CONSTRAINT_VIOLATION"):
            submit_scalar_probe(state, state, {"audio.end": 5, "subtitle.end": 6}, lambda s: s["audio.end"] == s["subtitle.end"])
        self.assertEqual(state, {"audio.end": 4, "subtitle.end": 4})

    def test_24_late_result_cannot_overwrite_adoption(self):
        with self.assertRaisesRegex(Invalid, "STALE_READ"):
            submit_scalar_probe({"usage.adopted": "user-v3"}, {"usage.adopted": "v1"}, {"usage.adopted": "late-v2"})

    def test_25_write_without_read_guard_rejected(self):
        with self.assertRaisesRegex(Invalid, "WRITE_PRECONDITION_REQUIRED"):
            submit_scalar_probe({"voice": 1}, {}, {"voice": 2})

    def test_26_track_must_account_for_its_inputs(self):
        self.obj("sequence")["payload"]["tracks"][0]["items"] = ["first"]
        with self.assertRaisesRegex(Invalid, "TRACK_MEMBERSHIP"):
            validate(self.p)

    def test_27_export_root_closure_excludes_unrelated_output(self):
        self.assertNotIn(("poster", 1), closure(self.p))
        self.assertIn(("font", 1), closure(self.p))

    def test_28_missing_output_port_rejected(self):
        self.obj("glow")["inputs"]["image"]["port"] = "not-a-port"
        with self.assertRaisesRegex(Invalid, "MISSING_PORT"):
            validate(self.p)

    def test_29_negative_placement_rejected(self):
        self.obj("fight-a")["payload"]["placement"]["start"] = {"n": "-1", "d": "1"}
        with self.assertRaisesRegex(Invalid, "NEGATIVE_PLACEMENT"):
            validate(self.p)

    def test_30_frozen_sample_at_duration_is_out_of_range(self):
        m = self.obj("fight-a")["payload"]["source_mapping"]
        m["source_start"], m["rate"] = {"n": "10", "d": "1"}, {"n": "0", "d": "1"}
        with self.assertRaisesRegex(Invalid, "SOURCE_BOUNDS"):
            validate(self.p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
