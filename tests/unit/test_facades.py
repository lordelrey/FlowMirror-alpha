"""Unit tests for the v7.1 five-layer facades and core Protocols.

Stdlib only (unittest). Run either way:

    python tests/unit/test_facades.py
    python -m unittest discover -s tests -p "test_facades.py" -v

The tests assert that every facade name is the *same object* as the
original it re-exports (so the facades add no behaviour), that
``core.types.ARM_MODALITY`` covers exactly the three arms, and that each
Protocol docstring states its layer and lag rule.
"""

import unittest
from typing import get_args

import flowmirror.agents.perceive as perceive_facade
import flowmirror.agents.prompt as prompt
import flowmirror.channels.feed as feed
import flowmirror.core as core
import flowmirror.core.protocols as protocols
import flowmirror.core.types as types
import flowmirror.engine.gm as gm
import flowmirror.engine.loop as loop
import flowmirror.engine.world as world
import flowmirror.platform as platform_facade
import flowmirror.population.sampler as sampler
import flowmirror.regulator.cn_cxr as cn_cxr
import flowmirror.society as society_facade


class CoreTypesTests(unittest.TestCase):
    def test_arm_modality_exactly_three_arms(self):
        self.assertEqual(set(types.ARM_MODALITY), {"T", "TC", "TV"})
        self.assertEqual(types.ARM_MODALITY["T"], "text")
        self.assertEqual(types.ARM_MODALITY["TC"], "image_as_text")
        self.assertEqual(types.ARM_MODALITY["TV"], "image")

    def test_modality_literal_matches_arm_values(self):
        self.assertEqual(set(get_args(types.Modality)), {"text", "image", "image_as_text"})
        self.assertTrue(set(types.ARM_MODALITY.values()) <= set(get_args(types.Modality)))

    def test_frozen_dataclasses(self):
        p = types.Percept("feed", "text", "x", "00")
        with self.assertRaises(AttributeError):
            p.channel = "news"
        with self.assertRaises(AttributeError):
            types.Action("post", {}) .type = "click"

    def test_core_package_reexports(self):
        self.assertIs(core.Percept, types.Percept)
        self.assertIs(core.Event, types.Event)
        self.assertIs(core.ARM_MODALITY, types.ARM_MODALITY)
        self.assertIs(core.Perceiver, protocols.Perceiver)
        self.assertIs(core.Experiment, protocols.Experiment)


class PlatformFacadeTests(unittest.TestCase):
    def test_same_objects(self):
        for name in (
            "hot_score",
            "climate_for",
            "top_comments",
            "rank_feed",
            "assign_arms",
            "arm_for_agent",
            "check_arm_balance",
        ):
            self.assertIs(getattr(platform_facade, name), getattr(feed, name), name)

    def test_all_list(self):
        self.assertEqual(
            sorted(platform_facade.__all__),
            sorted(
                [
                    "arm_for_agent",
                    "assign_arms",
                    "check_arm_balance",
                    "climate_for",
                    "hot_score",
                    "rank_feed",
                    "top_comments",
                ]
            ),
        )


class SocietyFacadeTests(unittest.TestCase):
    def test_same_objects(self):
        for name in ("load_world", "init_investors", "intent_probs", "publish_day"):
            self.assertIs(getattr(society_facade, name), getattr(world, name), name)
        self.assertIs(society_facade.sample_cohort, sampler.sample_cohort)


class GameMasterFacadeTests(unittest.TestCase):
    def test_same_objects(self):
        self.assertIs(gm.adjudicate_decision, loop.apply_decision)
        self.assertIs(gm.adapt_record, loop._adapt_record)
        self.assertIs(gm.cxr_outcome, cn_cxr.cxr_outcome)
        self.assertIs(gm.EventLog, world.EventLog)
        self.assertIs(gm.check_invariants, world.check_invariants)


class PerceiveFacadeTests(unittest.TestCase):
    def test_same_objects(self):
        for name in (
            "render_card",
            "render_direct",
            "render_experience",
            "render_news",
            "render_social",
            "render_trend",
        ):
            self.assertIs(getattr(perceive_facade, name), getattr(prompt, name), name)

    def test_perceivers_map(self):
        self.assertEqual(
            set(perceive_facade.PERCEIVERS),
            {"feed", "experience", "news", "trend", "social", "direct"},
        )
        self.assertIs(perceive_facade.PERCEIVERS["feed"], prompt.render_card)
        self.assertIs(perceive_facade.PERCEIVERS["experience"], prompt.render_experience)
        self.assertIs(perceive_facade.PERCEIVERS["news"], prompt.render_news)
        self.assertIs(perceive_facade.PERCEIVERS["trend"], prompt.render_trend)
        self.assertIs(perceive_facade.PERCEIVERS["social"], prompt.render_social)
        self.assertIs(perceive_facade.PERCEIVERS["direct"], prompt.render_direct)


class ProtocolTests(unittest.TestCase):
    def test_docstrings_state_layer_and_lag(self):
        for proto in (
            protocols.Perceiver,
            protocols.Memory,
            protocols.Deliberator,
            protocols.Actuator,
            protocols.GameMaster,
            protocols.Platform,
            protocols.InstitutionPolicy,
            protocols.Society,
            protocols.Experiment,
        ):
            self.assertIn("Layer:", proto.__doc__, proto.__name__)
            self.assertIn("Lag rule:", proto.__doc__, proto.__name__)

    def test_structural_conformance(self):
        class FakePerceiver:
            def perceive(self, view, agent_state, arm):
                return []

        class FakePlatform:
            def rank(self, items, ctx=None):
                return list(items)

            def aggregate(self, events):
                return {}

        self.assertIsInstance(FakePerceiver(), protocols.Perceiver)
        self.assertIsInstance(FakePlatform(), protocols.Platform)
        self.assertNotIsInstance(object(), protocols.Perceiver)
        self.assertNotIsInstance(FakePerceiver(), protocols.Platform)


if __name__ == "__main__":
    unittest.main(verbosity=2)
