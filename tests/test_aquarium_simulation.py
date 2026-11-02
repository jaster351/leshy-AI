from __future__ import annotations

import json
import pytest

from leshy.aquarium_simulation import AquariumSimulation, SpeciesProfileService


def test_verified_profiles_offline(tmp_path):
    service = SpeciesProfileService(tmp_path / "species.json")
    assert service.get("Paracheirodon innesi").schooling_min == 5
    assert service.get("Betta splendens").territorial is True
    assert service.get("Betta splendens").air_breather is True
    assert service.get("Amphiprion ocellaris").pair_bonding is True
    assert service.get("Amphiprion ocellaris").anemone_association is True


def test_wrong_environment_is_blocked(tmp_path):
    sim = AquariumSimulation(tmp_path)
    sim.reset("freshwater", 120)
    with pytest.raises(ValueError):
        sim.add_species("Amphiprion ocellaris", 2)
    sim.reset("marine", 200)
    with pytest.raises(ValueError):
        sim.add_species("Paracheirodon innesi", 6)


def test_neon_is_stressed_when_alone(tmp_path):
    sim = AquariumSimulation(tmp_path)
    sim.reset("freshwater", 120)
    sim.add_species("Paracheirodon innesi", 1)
    before = sim.fish[0].stress
    sim.tick(2)
    assert sim.fish[0].stress > before


def test_betta_crowding_increases_stress(tmp_path):
    sim = AquariumSimulation(tmp_path)
    sim.reset("freshwater", 60)
    sim.add_species("Betta splendens", 3)
    before = sum(f.stress for f in sim.fish)
    sim.tick(2)
    assert sum(f.stress for f in sim.fish) > before


def test_water_change_reduces_waste(tmp_path):
    sim = AquariumSimulation(tmp_path)
    sim.water.ammonia_mg_l = .2
    sim.water.nitrite_mg_l = .3
    sim.water.nitrate_mg_l = 50
    sim.apply_action({"type": "water_change", "percent": 25})
    assert sim.water.ammonia_mg_l == pytest.approx(.15)
    assert sim.water.nitrite_mg_l == pytest.approx(.225)
    assert sim.water.nitrate_mg_l == pytest.approx(37.5)


def test_leshy_step_executes_json_actions(tmp_path):
    class StubLLM:
        def chat(self, messages):
            return json.dumps({"thought_summary": "Кормлю умеренно.", "actions": [{"type": "feed", "amount": .8}]}, ensure_ascii=False)
    sim = AquariumSimulation(tmp_path)
    sim.reset("freshwater", 120)
    sim.add_species("Paracheirodon innesi", 6)
    for fish in sim.fish:
        fish.hunger = 75
    result = sim.leshy_step(StubLLM())
    assert result["actions"][0]["type"] == "feed"
    assert all(f.hunger < 75 for f in sim.fish)
