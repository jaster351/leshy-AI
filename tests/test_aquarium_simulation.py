from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from leshy.aquarium_simulation import AquariumSimulation, SpeciesProfileService


class AquariumSimulationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_disabled_aquarium_retains_state(self):
        allowed = {"value": True}
        sim = AquariumSimulation(self.root, enabled_check=lambda: allowed["value"])
        sim.reset()
        before = sim.snapshot()
        allowed["value"] = False
        for operation in (lambda: sim.tick(), lambda: sim.reset(),
                          lambda: sim.add_species("Betta splendens"),
                          lambda: sim.apply_action({"type": "feed"}),
                          lambda: sim.leshy_step(Mock())):
            with self.assertRaises(PermissionError):
                operation()
        self.assertEqual(before, sim.snapshot())

    def test_disable_during_leshy_decision_blocks_changes(self):
        allowed={"value": True}
        sim=AquariumSimulation(self.root, enabled_check=lambda: allowed["value"])
        sim.reset()
        before=sim.snapshot()
        model=Mock()
        def respond(messages):
            allowed["value"]=False
            return '{"actions":[{"type":"water_change","percent":20}]}'
        model.chat.side_effect=respond
        with self.assertRaises(PermissionError):
            sim.leshy_step(model)
        self.assertEqual(before, sim.snapshot())

    def test_verified_profiles_offline(self):
        service = SpeciesProfileService(self.root / "species.json")
        self.assertEqual(service.get("Paracheirodon innesi").schooling_min, 5)
        self.assertTrue(service.get("Betta splendens").territorial)
        self.assertTrue(service.get("Betta splendens").air_breather)
        self.assertTrue(service.get("Amphiprion ocellaris").pair_bonding)
        self.assertTrue(service.get("Amphiprion ocellaris").anemone_association)

    def test_wrong_environment_is_blocked(self):
        sim = AquariumSimulation(self.root)
        sim.reset("freshwater", 120)
        with self.assertRaises(ValueError):
            sim.add_species("Amphiprion ocellaris", 2)
        sim.reset("marine", 200)
        with self.assertRaises(ValueError):
            sim.add_species("Paracheirodon innesi", 6)

    def test_neon_is_stressed_when_alone(self):
        sim = AquariumSimulation(self.root)
        sim.reset("freshwater", 120)
        sim.add_species("Paracheirodon innesi", 1)
        before = sim.fish[0].stress
        sim.tick(2)
        self.assertGreater(sim.fish[0].stress, before)

    def test_betta_crowding_increases_stress(self):
        sim = AquariumSimulation(self.root)
        sim.reset("freshwater", 60)
        sim.add_species("Betta splendens", 3)
        before = sum(f.stress for f in sim.fish)
        sim.tick(2)
        self.assertGreater(sum(f.stress for f in sim.fish), before)

    def test_water_change_reduces_waste(self):
        sim = AquariumSimulation(self.root)
        sim.water.ammonia_mg_l = .2
        sim.water.nitrite_mg_l = .3
        sim.water.nitrate_mg_l = 50
        sim.apply_action({"type": "water_change", "percent": 25})
        self.assertAlmostEqual(sim.water.ammonia_mg_l, .15)
        self.assertAlmostEqual(sim.water.nitrite_mg_l, .225)
        self.assertAlmostEqual(sim.water.nitrate_mg_l, 37.5)

    def test_leshy_step_executes_json_actions(self):
        class StubLLM:
            def chat(self, messages):
                return json.dumps({"thought_summary": "Кормлю умеренно.", "actions": [{"type": "feed", "amount": .8}]}, ensure_ascii=False)
        sim = AquariumSimulation(self.root)
        sim.reset("freshwater", 120)
        sim.add_species("Paracheirodon innesi", 6)
        for fish in sim.fish:
            fish.hunger = 75
        result = sim.leshy_step(StubLLM())
        self.assertEqual(result["actions"][0]["type"], "feed")
        self.assertTrue(all(f.hunger < 75 for f in sim.fish))


if __name__ == "__main__":
    unittest.main()
