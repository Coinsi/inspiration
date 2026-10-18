"""Adversarial design checks for draft 0.2; not production media processing.

Run all probes: python -m unittest discover -s docs/architecture/editable-project
  -p "verify*.py" -v
"""
from __future__ import annotations

import copy
import itertools
import json
import math
import unittest
from fractions import Fraction

import verify_design as d


Invalid = d.Invalid
KINDS = d.KNOWN_KINDS
# Trusted fixture definitions; these are NOT implementations of the operators.
PORTS = {
    "studio.trail@1": ({"motion": "transform_track"}, {"image": "video"}),
    "studio.glow@1": ({"image": "video"}, {"image": "video"}),
    "studio.occluded_over@1": ({"background": "video", "glow": "video", "occlusion": "mask"}, {"video": "video"}),
    "studio.lyrics@1": ({"font": "font", "music": "audio"}, {"video": "video"}),
}


def finite_tree(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise Invalid("NON_FINITE_NUMBER")
    if isinstance(value, dict):
        for v in value.values():
            finite_tree(v)
    elif isinstance(value, list):
        for v in value:
            finite_tree(v)


def number(value, lo, hi):
    if type(value) not in (int, float) or not math.isfinite(value) or not lo <= value <= hi:
        raise Invalid("PARAMETER_RANGE")


def parameter(value, duration, lo=0, hi=1):
    mode = value.get("mode")
    if mode == "constant":
        if set(value) != {"mode", "value", "unit"} or value["unit"] != "ratio":
            raise Invalid("PARAMETER_SHAPE")
        number(value["value"], lo, hi)
    elif mode == "curve":
        if set(value) != {"mode", "unit", "keys"} or value["unit"] != "ratio" or not value["keys"]:
            raise Invalid("PARAMETER_SHAPE")
        previous, ids = None, set()
        for key in value["keys"]:
            t = d.time_value(key["time"])
            if not 0 <= t <= duration or previous is not None and t <= previous:
                raise Invalid("KEYFRAME_TIME")
            if not isinstance(key["id"], str) or not key["id"] or key["id"] in ids:
                raise Invalid("KEYFRAME_ID")
            if key["interpolation"] not in {"linear", "step"}:
                raise Invalid("UNSUPPORTED_PROBE_INTERPOLATION")
            ids.add(key["id"])
            number(key["value"], lo, hi)
            previous = t
    else:
        raise Invalid("UNSUPPORTED_PROBE_PARAMETER_MODE")


def validate(package):
    finite_tree(package)
    objects = d.validate(package)
    for root in package["commit"]["roots"].values():
        streams = root.get("streams", {})
        if not streams or any(set(s) != {"port", "accepts"} for s in streams.values()):
            raise Invalid("OUTPUT_STREAM_SHAPE")
        p = root.get("profile", {})
        required = {"av": ["audio", "video"], "image": ["image"]}.get(p.get("kind"))
        if required is None or sorted(s["accepts"] for s in streams.values()) != required:
            raise Invalid("OUTPUT_PROFILE_STREAMS")
        for dim in ("width", "height"):
            if type(p.get(dim)) is not int or p[dim] <= 0:
                raise Invalid("OUTPUT_DIMENSIONS")
        if p["kind"] == "av":
            if d.time_value(p["frame_rate"]) <= 0 or type(p.get("sample_rate")) is not int or p["sample_rate"] <= 0:
                raise Invalid("OUTPUT_TIMEBASE")
            if p.get("channel_layout") not in {"mono", "stereo"}:
                raise Invalid("UNSUPPORTED_PROBE_CHANNELS")
    for obj in objects.values():
        p, kind = obj["payload"], obj["kind"]
        # Unknown schemas are retained; interpreting their payload is forbidden.
        if kind not in KINDS or obj["schema_version"] != 1:
            continue
        op = p.get("operator")
        if op in PORTS:
            ins, outs = PORTS[op]
            if {n: r["accepts"] for n, r in obj["inputs"].items()} != ins or obj["outputs"] != outs:
                raise Invalid("OPERATOR_PORT_CONTRACT")
        if op == "studio.glow@1":
            params = p.get("parameters", {})
            if set(params) != {"intensity"}:
                raise Invalid("UNKNOWN_PARAMETER")
            parameter(params["intensity"], d.duration(obj))
        if kind == "instance":
            source_type = obj["inputs"]["source"]["accepts"]
            if set(obj["outputs"].values()) != {source_type}:
                raise Invalid("INSTANCE_TYPE_CONVERSION")
            if source_type == "audio" and d.time_value(p["source_mapping"]["rate"]) == 0:
                raise Invalid("AUDIO_FREEZE_POLICY_REQUIRED")
            if "gain_db" in p:
                number(p["gain_db"], -120, 24)
        if kind == "sequence":
            ids = [t["id"] for t in p["tracks"]]
            if len(ids) != len(set(ids)):
                raise Invalid("TRACK_ID")
            for track in p["tracks"]:
                mode = track.get("overlap_policy")
                if mode not in {"reject", "mix", "stack"}:
                    raise Invalid("OVERLAP_POLICY_REQUIRED")
                if (mode == "mix" and track["kind"] != "audio") or (mode == "stack" and track["kind"] != "video"):
                    raise Invalid("TRACK_COMBINE_POLICY")
                if mode == "reject":
                    ranges = sorted((
                        d.time_value(objects[d.key(obj["inputs"][n]["target"])]["payload"]["placement"]["start"]),
                        d.duration(objects[d.key(obj["inputs"][n]["target"])]),
                    ) for n in track["items"])
                    for (start, length), (next_start, _) in zip(ranges, ranges[1:]):
                        if start + length > next_start:
                            raise Invalid("UNDECLARED_OVERLAP")
        if op == "studio.lyrics@1":
            ids = set()
            for cue in p["cues"]:
                if cue["id"] in ids:
                    raise Invalid("CUE_ID")
                ids.add(cue["id"])
                start, end = d.time_value(cue["start"]), d.time_value(cue["end"])
                if not 0 <= start < end <= d.duration(obj):
                    raise Invalid("CUE_RANGE")
                if not isinstance(cue["text"], str) or not cue["text"].strip():
                    raise Invalid("CUE_TEXT")
    return objects


def unsupported(package, root="main"):
    objects = validate(package)
    missing = set()
    for k in d.closure(package, root):
        obj = objects[k]
        if obj["kind"] not in KINDS or obj["schema_version"] != 1:
            missing.add(f'type:{obj["kind"]}@{obj["schema_version"]}')
        op = obj["payload"].get("operator")
        if obj["kind"] in {"composition", "effect"} and not op:
            missing.add(f'implementation:{obj["id"]}')
        if op and op not in PORTS:
            missing.add(op)
    return missing


def resolve_anchor(anchor, positions, revisions):
    """Simple one-instance time anchor model; no cross-document retiming."""
    if anchor["mode"] == "absolute":
        return d.time_value(anchor["time"])
    target = anchor["target"]
    if target not in positions:
        raise Invalid("ANCHOR_TARGET_MISSING")
    if revisions.get(target) != anchor["content_revision"]:
        raise Invalid("ANCHOR_NEEDS_REVIEW")
    start, source_start, rate, length = positions[target]
    if anchor["mode"] == "local":
        t = d.time_value(anchor["time"])
    elif anchor["mode"] == "source":
        if rate == 0:
            raise Invalid("ANCHOR_MAPPING_AMBIGUOUS")
        t = (d.time_value(anchor["time"]) - source_start) / rate
    else:
        raise Invalid("ANCHOR_MODE")
    if not 0 <= t < length:
        raise Invalid("ANCHOR_OUTSIDE_INSTANCE")
    return start + t


def invalidated_range(changed, total, support):
    """Forward affected interval, different from an output's input read window."""
    a, b = changed
    if not 0 <= a < b <= total:
        raise Invalid("CHANGE_RANGE")
    kind = support["kind"]
    if kind == "frame_local":
        return a, b
    if kind == "window":
        before, after = support["before"], support["after"]
        if before < 0 or after < 0:
            raise Invalid("WINDOW_RANGE")
        # output t reads [t-before, t+after]; changed input shifts the other way.
        return max(Fraction(0), a - after), min(total, b + before)
    if kind == "stateful":
        return a, total
    if kind in {"whole_input", "unknown"}:
        return Fraction(0), total
    raise Invalid("UNKNOWN_TEMPORAL_POLICY")


def versioned_submit(state, expected, writes, protected=frozenset(), invariant=lambda _: True):
    """A pure state transition model, not a multi-process transaction."""
    if set(writes) & protected:
        raise Invalid("PROTECTED_TARGET")
    if not set(writes) <= set(expected):
        raise Invalid("WRITE_PRECONDITION_REQUIRED")
    if any(k not in state or state[k]["revision"] != rev for k, rev in expected.items()):
        raise Invalid("STALE_READ")
    result = copy.deepcopy(state)
    for k, value in writes.items():
        result[k] = {"revision": state[k]["revision"] + 1, "value": value}
    if not invariant(result):
        raise Invalid("CONSTRAINT_VIOLATION")
    return result


class ContractEdges(unittest.TestCase):
    def setUp(self):
        self.p = json.loads(d.FIXTURE.read_text(encoding="utf-8"))

    def obj(self, name):
        return d.index(self.p)[name, 1]

    def test_31_output_binds_video_and_sound_to_one_revision(self):
        validate(self.p)
        self.assertEqual(set(self.p["commit"]["roots"]["main"]["streams"]), {"picture", "sound"})
        new = d.edit_usage(self.p, "main", ["voice"], lambda o: o["payload"].update(gain_db=3))
        root = new["commit"]["roots"]["main"]
        self.assertEqual(root["target"], {"id": "sequence", "revision": 2})
        validate(new)

    def test_32_video_audio_cannot_pin_different_root_revisions(self):
        self.p["commit"]["roots"]["main"]["streams"]["sound"]["target"] = {"id": "sequence", "revision": 9}
        with self.assertRaises(Invalid):
            validate(self.p)

    def test_33_av_output_cannot_accidentally_lose_sound(self):
        del self.p["commit"]["roots"]["main"]["streams"]["sound"]
        with self.assertRaisesRegex(Invalid, "OUTPUT_PROFILE_STREAMS"):
            validate(self.p)

    def test_34_forged_port_declaration_is_not_authority(self):
        self.obj("glow")["inputs"]["image"] = {"target": {"id": "music", "revision": 1}, "port": "audio", "accepts": "audio"}
        with self.assertRaisesRegex(Invalid, "OPERATOR_PORT_CONTRACT"):
            validate(self.p)

    def test_35_unknown_document_type_is_preserved_and_blocked(self):
        self.obj("poster")["kind"] = "future.scene"
        saved = json.loads(json.dumps(self.p))
        self.assertEqual(saved, self.p)
        self.assertEqual(unsupported(saved, "poster"), {"type:future.scene@1"})

    def test_36_new_known_type_schema_is_not_silently_executed(self):
        self.obj("poster")["schema_version"] = 2
        self.assertEqual(unsupported(self.p, "poster"), {"type:image_document@2"})

    def test_37_effect_with_no_implementation_is_not_renderable(self):
        del self.obj("glow")["payload"]["operator"]
        self.assertIn("implementation:glow", unsupported(self.p))

    def test_38_cue_cannot_exceed_composition(self):
        self.obj("lyrics")["payload"]["cues"][1]["end"] = {"n": "9", "d": "1"}
        with self.assertRaisesRegex(Invalid, "CUE_RANGE"):
            validate(self.p)

    def test_39_cues_have_stable_unique_identity(self):
        self.obj("lyrics")["payload"]["cues"][1]["id"] = "line-1"
        with self.assertRaisesRegex(Invalid, "CUE_ID"):
            validate(self.p)

    def test_40_parameter_rejects_nan_and_infinity(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                self.obj("glow")["payload"]["parameters"]["intensity"]["value"] = value
                with self.assertRaisesRegex(Invalid, "NON_FINITE_NUMBER"):
                    validate(self.p)

    def test_41_parameter_range_and_mixed_modes_rejected(self):
        v = self.obj("glow")["payload"]["parameters"]["intensity"]
        v["value"] = -1
        with self.assertRaisesRegex(Invalid, "PARAMETER_RANGE"):
            validate(self.p)
        v["value"] = 0.5
        v["keys"] = []
        with self.assertRaisesRegex(Invalid, "PARAMETER_SHAPE"):
            validate(self.p)

    def test_42_curve_keys_must_be_ordered_and_in_range(self):
        curve = {"mode": "curve", "unit": "ratio", "keys": [
            {"id": "a", "time": {"n": "0", "d": "1"}, "value": 0, "interpolation": "linear"},
            {"id": "b", "time": {"n": "10", "d": "1"}, "value": 1, "interpolation": "linear"},
        ]}
        self.obj("glow")["payload"]["parameters"]["intensity"] = curve
        validate(self.p)
        curve["keys"][1]["time"] = {"n": "11", "d": "1"}
        with self.assertRaisesRegex(Invalid, "KEYFRAME_TIME"):
            validate(self.p)
        curve["keys"][1]["time"] = {"n": "0", "d": "1"}
        with self.assertRaisesRegex(Invalid, "KEYFRAME_TIME"):
            validate(self.p)

    def test_43_same_track_overlap_needs_explicit_policy(self):
        self.obj("fight-b")["payload"]["placement"]["start"] = {"n": "2", "d": "1"}
        with self.assertRaisesRegex(Invalid, "UNDECLARED_OVERLAP"):
            validate(self.p)
        self.obj("sequence")["payload"]["tracks"][0]["overlap_policy"] = "stack"
        validate(self.p)

    def test_44_audio_freeze_is_not_video_freeze(self):
        self.obj("voice-use")["payload"]["source_mapping"]["rate"] = {"n": "0", "d": "1"}
        with self.assertRaisesRegex(Invalid, "AUDIO_FREEZE_POLICY_REQUIRED"):
            validate(self.p)

    def test_45_local_anchor_moves_but_absolute_anchor_does_not(self):
        positions = {"clip": (Fraction(13), Fraction(2), Fraction(1), Fraction(4))}
        anchor = {"mode": "local", "target": "clip", "content_revision": 1, "time": {"n": "1", "d": "1"}}
        self.assertEqual(resolve_anchor(anchor, positions, {"clip": 1}), 14)
        self.assertEqual(resolve_anchor({"mode": "absolute", "time": {"n": "11", "d": "1"}}, positions, {}), 11)

    def test_46_source_anchor_handles_half_speed(self):
        pos = {"clip": (Fraction(10), Fraction(2), Fraction(1, 2), Fraction(4))}
        a = {"mode": "source", "target": "clip", "content_revision": 1, "time": {"n": "3", "d": "1"}}
        self.assertEqual(resolve_anchor(a, pos, {"clip": 1}), 12)

    def test_47_anchor_deleted_or_regenerated_requires_repair(self):
        a = {"mode": "local", "target": "clip", "content_revision": 1, "time": {"n": "1", "d": "1"}}
        with self.assertRaisesRegex(Invalid, "ANCHOR_TARGET_MISSING"):
            resolve_anchor(a, {}, {})
        with self.assertRaisesRegex(Invalid, "ANCHOR_NEEDS_REVIEW"):
            resolve_anchor(a, {"clip": (0, 0, 1, 4)}, {"clip": 2})

    def test_48_freeze_has_no_unique_source_to_output_time(self):
        a = {"mode": "source", "target": "clip", "content_revision": 1, "time": {"n": "2", "d": "1"}}
        with self.assertRaisesRegex(Invalid, "ANCHOR_MAPPING_AMBIGUOUS"):
            resolve_anchor(a, {"clip": (0, 2, 0, 4)}, {"clip": 1})

    def test_49_trail_propagates_changes_forward_not_backward(self):
        support = {"kind": "window", "before": Fraction(1), "after": Fraction(0)}
        self.assertEqual(invalidated_range((Fraction(2), Fraction(3)), Fraction(10), support), (2, 4))

    def test_50_lookahead_propagates_backwards(self):
        support = {"kind": "window", "before": Fraction(0), "after": Fraction(1)}
        self.assertEqual(invalidated_range((Fraction(2), Fraction(3)), Fraction(10), support), (1, 3))

    def test_51_stateful_and_unknown_invalidation_is_conservative(self):
        self.assertEqual(invalidated_range((2, 3), 10, {"kind": "stateful"}), (2, 10))
        self.assertEqual(invalidated_range((2, 3), 10, {"kind": "unknown"}), (0, 10))

    def test_52_same_value_after_two_edits_is_still_stale(self):
        s = {"voice": {"revision": 1, "value": "A"}}
        s = versioned_submit(s, {"voice": 1}, {"voice": "B"})
        s = versioned_submit(s, {"voice": 2}, {"voice": "A"})
        with self.assertRaisesRegex(Invalid, "STALE_READ"):
            versioned_submit(s, {"voice": 1}, {"voice": "C"})

    def test_53_all_disjoint_submission_orders_converge(self):
        for order in itertools.permutations(("voice", "poster", "lyrics")):
            state = {k: {"revision": 1, "value": 0} for k in order}
            for k in order:
                state = versioned_submit(state, {k: 1}, {k: 1})
            self.assertTrue(all(v == {"revision": 2, "value": 1} for v in state.values()))

    def test_54_all_conflicting_orders_reject_second_writer(self):
        for order in itertools.permutations(("A", "B", "C")):
            state = {"voice": {"revision": 1, "value": "original"}}
            state = versioned_submit(state, {"voice": 1}, {"voice": order[0]})
            for value in order[1:]:
                with self.assertRaisesRegex(Invalid, "STALE_READ"):
                    versioned_submit(state, {"voice": 1}, {"voice": value})

    def test_55_protection_is_enforced_below_agent(self):
        with self.assertRaisesRegex(Invalid, "PROTECTED_TARGET"):
            versioned_submit({"shot": {"revision": 1, "value": "approved"}}, {"shot": 1}, {"shot": "other"}, {"shot"})

    def test_56_failed_group_leaves_versions_and_values_unchanged(self):
        s = {"voice": {"revision": 1, "value": 4}, "subtitle": {"revision": 1, "value": 4}}
        before = copy.deepcopy(s)
        with self.assertRaisesRegex(Invalid, "CONSTRAINT_VIOLATION"):
            versioned_submit(s, {k: 1 for k in s}, {"voice": 5, "subtitle": 6}, invariant=lambda c: c["voice"]["value"] == c["subtitle"]["value"])
        self.assertEqual(s, before)

    def test_57_instance_cannot_lie_about_output_type(self):
        self.obj("voice-use")["outputs"]["audio"] = "video"
        self.obj("sequence")["inputs"]["voice"]["accepts"] = "video"
        self.obj("sequence")["payload"]["tracks"][-1]["kind"] = "video"
        self.obj("sequence")["payload"]["tracks"][-1]["overlap_policy"] = "stack"
        with self.assertRaisesRegex(Invalid, "INSTANCE_TYPE_CONVERSION"):
            validate(self.p)

    def test_58_output_stream_cannot_smuggle_own_target(self):
        self.p["commit"]["roots"]["main"]["streams"]["sound"]["target"] = {"id": "sequence", "revision": 1}
        with self.assertRaisesRegex(Invalid, "OUTPUT_STREAM_SHAPE"):
            validate(self.p)

    def test_59_unknown_parameter_not_silently_ignored(self):
        self.obj("glow")["payload"]["parameters"]["typo_intensity"] = {"mode": "constant", "value": 0.1, "unit": "ratio"}
        with self.assertRaisesRegex(Invalid, "UNKNOWN_PARAMETER"):
            validate(self.p)

    def test_60_opaque_kind_has_no_unrelated_render_impact(self):
        self.obj("poster")["kind"] = "future.scene"
        self.assertEqual(unsupported(self.p, "main"), set())

    def test_61_unknown_payload_is_not_interpreted_as_known_schema(self):
        self.obj("poster")["kind"] = "future.scene"
        self.obj("poster")["payload"] = {"duration": {"future_unit": "preserve me"}}
        before = copy.deepcopy(self.p)
        self.assertEqual(unsupported(self.p, "poster"), {"type:future.scene@1"})
        self.assertEqual(self.p, before)

    def test_62_new_schema_payload_is_opaque(self):
        self.obj("poster")["schema_version"] = 2
        self.obj("poster")["payload"] = {"duration": "not a v1 time"}
        self.assertEqual(unsupported(self.p, "poster"), {"type:image_document@2"})

    def test_63_opaque_source_keeps_usage_but_blocks_output_preflight(self):
        self.obj("sword-comp")["kind"] = "future.composition"
        self.obj("sword-comp")["payload"] = {"duration": {"future_unit": "opaque"}}
        self.assertIn("type:future.composition@1", unsupported(self.p))

    def test_64_candidate_creation_retains_entire_old_dependency_closure(self):
        before = copy.deepcopy(self.p)
        new = d.edit_usage(self.p, "main", ["first", "source", "glow"], lambda o: o["payload"]["parameters"]["intensity"].update(value=0.3))
        old_index, new_index = d.index(before), d.index(new)
        for k in d.closure(before):
            self.assertEqual(old_index[k], new_index[k])
        self.assertEqual(d.closure(before), d.closure(self.p))


if __name__ == "__main__":
    unittest.main(verbosity=2)
