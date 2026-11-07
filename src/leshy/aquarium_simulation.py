from __future__ import annotations

from dataclasses import asdict, dataclass, field
from html import unescape
import json
import math
from pathlib import Path
import random
import re
import time
from typing import Any
from urllib import error, parse, request

from .llm import ChatMessage

FISHBASE_API = "https://fishbase.ropensci.org"
FISHBASE_SUMMARY = "https://www.fishbase.se/summary/{slug}"
WORMS_REST = "https://www.marinespecies.org/rest"


@dataclass(slots=True)
class SpeciesProfile:
    scientific_name: str
    common_name: str = ""
    environments: list[str] = field(default_factory=list)
    habitat: str = ""
    temperature_min_c: float | None = None
    temperature_max_c: float | None = None
    ph_min: float | None = None
    ph_max: float | None = None
    dh_min: float | None = None
    dh_max: float | None = None
    max_length_cm: float | None = None
    schooling_min: int = 1
    aggression: float = 0.15
    territorial: bool = False
    pair_bonding: bool = False
    air_breather: bool = False
    anemone_association: bool = False
    diet: str = "unknown"
    confidence: float = 0.35
    evidence: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    fetched_at: float = 0.0

    def supports_mode(self, mode: str) -> bool:
        values = {item.lower() for item in self.environments}
        if mode == "freshwater":
            return "freshwater" in values or "brackish" in values
        return "marine" in values or "saltwater" in values or "brackish" in values


_VERIFIED = {
    "paracheirodon innesi": SpeciesProfile(
        "Paracheirodon innesi", "Neon tetra", ["freshwater"], "pelagic",
        20.0, 26.0, 5.0, 7.0, 1.0, 2.0, 2.5, 5, 0.05,
        diet="omnivore: worms, insects, crustaceans, plant matter", confidence=0.98,
        evidence=["FishBase: freshwater, pH 5-7, dH 1-2, 20-26 C.", "FishBase: aquarium groups of 5 or more."],
        source_urls=["https://www.fishbase.se/summary/Paracheirodon-innesi"],
    ),
    "betta splendens": SpeciesProfile(
        "Betta splendens", "Siamese fighting fish", ["freshwater"], "benthopelagic",
        24.0, 30.0, 6.0, 8.0, 5.0, 19.0, 6.5, 1, 0.95, True, False, True, False,
        "carnivore/insectivore", confidence=0.98,
        evidence=["FishBase: freshwater, pH 6-8, dH 5-19, 24-30 C.", "FishBase: air breather; males fight each other."],
        source_urls=["https://www.fishbase.se/summary/Betta-splendens"],
    ),
    "amphiprion ocellaris": SpeciesProfile(
        "Amphiprion ocellaris", "Clown anemonefish", ["marine"], "reef-associated",
        max_length_cm=11.0, schooling_min=2, aggression=0.35, territorial=True,
        pair_bonding=True, anemone_association=True, diet="omnivore", confidence=0.95,
        evidence=["FishBase: marine reef fish.", "FishBase: distinct pair is monogamous; association with host anemones."],
        source_urls=["https://www.fishbase.se/summary/Amphiprion-ocellaris"],
    ),
    "zebrasoma flavescens": SpeciesProfile(
        "Zebrasoma flavescens", "Yellow tang", ["marine"], "reef-associated",
        24.0, 28.0, max_length_cm=20.0, schooling_min=1, aggression=0.45, territorial=True,
        diet="herbivore: filamentous algae", confidence=0.97,
        evidence=["FishBase: marine reef fish, 24-28 C.", "FishBase: singly or loose groups; herbivorous; territorial males."],
        source_urls=["https://www.fishbase.se/summary/Zebrasoma-flavescens"],
    ),
}


def _num(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _first(data: dict[str, Any], *keys: str) -> Any:
    lowered = {str(k).lower(): v for k, v in data.items()}
    for key in keys:
        if key in data:
            return data[key]
        if key.lower() in lowered:
            return lowered[key.lower()]
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _plain_html(payload: str) -> str:
    payload = re.sub(r"(?is)<script.*?>.*?</script>|<style.*?>.*?</style>", " ", payload)
    return " ".join(unescape(re.sub(r"(?s)<[^>]+>", " ", payload)).split())


class SpeciesProfileService:
    """FishBase-first profiles; unknown traits stay unknown instead of being invented."""

    def __init__(self, cache_path: Path, timeout: int = 8):
        self.cache_path = cache_path
        self.timeout = timeout
        self.cache: dict[str, SpeciesProfile] = {k: SpeciesProfile(**asdict(v)) for k, v in _VERIFIED.items()}
        if cache_path.exists():
            try:
                for item in json.loads(cache_path.read_text(encoding="utf-8")).values():
                    profile = SpeciesProfile(**item)
                    self.cache[profile.scientific_name.lower()] = profile
            except (OSError, ValueError, TypeError):
                pass

    def get(self, scientific_name: str, refresh: bool = False) -> SpeciesProfile:
        name = " ".join(str(scientific_name or "").split()).strip()
        if len(name.split()) < 2:
            raise ValueError("Укажите научное название, например Betta splendens")
        key = name.lower()
        if key in self.cache and not refresh:
            return SpeciesProfile(**asdict(self.cache[key]))
        profile = self._fetch(name)
        if profile is None:
            if key in self.cache:
                return SpeciesProfile(**asdict(self.cache[key]))
            raise LookupError(f"FishBase не вернул данные для {name}")
        if "marine" in {x.lower() for x in profile.environments}:
            self._worms(profile)
        self.cache[key] = profile
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps({k: asdict(v) for k, v in self.cache.items()}, ensure_ascii=False, indent=2), encoding="utf-8")
        return SpeciesProfile(**asdict(profile))

    def _get_json(self, url: str) -> Any:
        req = request.Request(url, headers={"User-Agent": "Leshy-Aquarium/0.1"})
        with request.urlopen(req, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _fetch(self, name: str) -> SpeciesProfile | None:
        genus, species = name.split()[:2]
        api = f"{FISHBASE_API}/species?{parse.urlencode({'Genus': genus, 'Species': species, 'limit': 1})}"
        row: dict[str, Any] = {}
        try:
            payload = self._get_json(api)
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            if rows:
                row = dict(rows[0])
        except Exception:
            pass
        summary = FISHBASE_SUMMARY.format(slug=parse.quote(f"{genus}-{species}"))
        text = ""
        try:
            req = request.Request(summary, headers={"User-Agent": "Leshy-Aquarium/0.1"})
            with request.urlopen(req, timeout=self.timeout) as response:
                text = _plain_html(response.read().decode("utf-8", errors="replace"))
        except Exception:
            pass
        if not row and not text:
            return None
        lower = text.lower()
        env: list[str] = []
        if _truthy(_first(row, "Fresh")) or "freshwater" in lower:
            env.append("freshwater")
        if _truthy(_first(row, "Brack")) or "brackish" in lower:
            env.append("brackish")
        if _truthy(_first(row, "Saltwater")) or "marine" in lower:
            env.append("marine")
        p = SpeciesProfile(
            f"{genus} {species}", str(_first(row, "FBname") or ""), env,
            str(_first(row, "DemersPelag", "Habitat") or "").lower(),
            _num(_first(row, "TempMin")), _num(_first(row, "TempMax")),
            _num(_first(row, "pHMin")), _num(_first(row, "pHMax")),
            _num(_first(row, "dHMin")), _num(_first(row, "dHMax")),
            _num(_first(row, "Length", "MaxLength")), confidence=0.65 if row else 0.5,
            source_urls=[api, summary], fetched_at=time.time(),
        )
        school = re.search(r"groups?\s+of\s+(\d+)\s+or\s+more", lower)
        if school:
            p.schooling_min = max(1, min(100, int(school.group(1))))
            p.evidence.append(f"FishBase: group of {p.schooling_min} or more.")
        if any(x in lower for x in ("males will fight each other", "highly territorial", "territorial males")):
            p.territorial, p.aggression = True, 0.85
            p.evidence.append("FishBase: territorial/aggressive behaviour.")
        elif "territorial" in lower:
            p.territorial, p.aggression = True, 0.5
        if "monogamous" in lower or "distinct pair" in lower:
            p.pair_bonding, p.schooling_min = True, max(2, p.schooling_min)
        if "air breather" in lower:
            p.air_breather = True
        if "sea anemone" in lower or "anemones:" in lower:
            p.anemone_association = True
        if "herbivorous" in lower:
            p.diet = "herbivore"
        elif "zooplankton" in lower or "insect larvae" in lower:
            p.diet = "carnivore/insectivore"
        elif "omnivorous" in lower:
            p.diet = "omnivore"
        if "reef-associated" in lower:
            p.habitat = "reef-associated"
        p.confidence = min(0.92, p.confidence + 0.03 * len(p.evidence))
        return p

    def _worms(self, profile: SpeciesProfile) -> None:
        url = f"{WORMS_REST}/AphiaRecordsByName/{parse.quote(profile.scientific_name)}?like=false&marine_only=true&offset=1"
        try:
            records = self._get_json(url)
            if isinstance(records, list) and records:
                profile.source_urls.append(url)
                profile.evidence.append("WoRMS: marine taxon confirmed.")
                profile.confidence = min(0.99, profile.confidence + 0.05)
        except Exception:
            pass


@dataclass(slots=True)
class FishState:
    id: str
    scientific_name: str
    common_name: str
    health: float = 100.0
    stress: float = 10.0
    hunger: float = 15.0
    energy: float = 85.0
    alive: bool = True
    age_hours: float = 0.0
    x: float = 0.5
    y: float = 0.5


@dataclass(slots=True)
class WaterState:
    temperature_c: float
    ph: float
    dissolved_oxygen_mg_l: float
    ammonia_mg_l: float
    nitrite_mg_l: float
    nitrate_mg_l: float
    salinity_ppt: float
    gh_dh: float
    kh_dh: float
    clarity: float = 1.0


@dataclass(slots=True)
class EquipmentState:
    filter_on: bool = True
    filter_efficiency: float = 0.90
    heater_setpoint_c: float = 25.0
    aeration_on: bool = True
    light_on: bool = True
    skimmer_on: bool = False
    auto_topoff_on: bool = True


class AquariumSimulation:
    def __init__(self, brain_dir: Path, rng_seed: int = 351):
        self.data_dir = Path(brain_dir) / "data"
        self.state_path = self.data_dir / "aquarium_state.json"
        self.species = SpeciesProfileService(self.data_dir / "aquarium_species_cache.json")
        self.rng = random.Random(rng_seed)
        self.mode, self.volume_l, self.sim_hour = "freshwater", 120.0, 0.0
        self.water = self._default_water("freshwater")
        self.equipment = EquipmentState()
        self.fish: list[FishState] = []
        self.plants_health, self.plant_biomass, self.feed_residue = 90.0, 1.0, 0.0
        self.events: list[dict[str, Any]] = []
        self.last_leshy_message = ""
        self.last_leshy_actions: list[dict[str, Any]] = []
        self._load()

    @staticmethod
    def _default_water(mode: str) -> WaterState:
        if mode == "marine":
            return WaterState(25.5, 8.15, 7.2, 0.0, 0.0, 2.0, 35.0, 8.0, 8.0)
        return WaterState(25.0, 7.0, 7.5, 0.0, 0.0, 8.0, 0.0, 8.0, 5.0)

    def _event(self, kind: str, message: str, actor: str = "simulation") -> None:
        self.events.append({"sim_hour": round(self.sim_hour, 3), "kind": kind, "message": message, "actor": actor})
        self.events = self.events[-250:]

    def reset(self, mode: str = "freshwater", volume_l: float = 120.0) -> None:
        mode = mode.strip().lower()
        if mode not in {"freshwater", "marine"}:
            raise ValueError("mode должен быть freshwater или marine")
        self.mode, self.volume_l, self.sim_hour = mode, max(20.0, min(volume_l, 5000.0)), 0.0
        self.water = self._default_water(mode)
        self.equipment = EquipmentState(skimmer_on=mode == "marine")
        self.fish, self.events, self.last_leshy_actions = [], [], []
        self.plants_health, self.plant_biomass, self.feed_residue, self.last_leshy_message = 90.0, 1.0, 0.0, ""
        self._event("reset", f"Создан {'морской' if mode == 'marine' else 'пресноводный'} аквариум {self.volume_l:g} л.", "user")
        self._save()

    def add_species(self, scientific_name: str, count: int = 1) -> SpeciesProfile:
        p = self.species.get(scientific_name)
        if not p.supports_mode(self.mode):
            raise ValueError(f"{p.scientific_name} не подтверждён для {self.mode}; источники: {', '.join(p.environments) or 'неизвестно'}")
        count = max(1, min(int(count), 50))
        for _ in range(count):
            self.fish.append(FishState(f"fish-{time.time_ns()}-{self.rng.randrange(99999)}", p.scientific_name, p.common_name or p.scientific_name, x=self.rng.uniform(.12,.88), y=self.rng.uniform(.18,.78)))
        self._event("stocking", f"Добавлено: {p.scientific_name} × {count}.", "user")
        self._save()
        return p

    def _profile(self, name: str) -> SpeciesProfile:
        try:
            return self.species.get(name)
        except Exception:
            return SpeciesProfile(name, environments=[self.mode], confidence=.1)

    @staticmethod
    def _range_penalty(value: float, low: float | None, high: float | None, scale: float) -> float:
        if low is None or high is None or low <= value <= high:
            return 0.0
        return min(45.0, 8.0 + (low - value if value < low else value - high) * scale)

    def tick(self, hours: float = 1.0) -> dict[str, Any]:
        hours = max(.05, min(float(hours), 24.0))
        steps = max(1, int(math.ceil(hours / .25)))
        for _ in range(steps):
            self._tick_small(hours / steps)
        self._save()
        return self.snapshot()

    def _tick_small(self, dt: float) -> None:
        self.sim_hour += dt
        alive = [f for f in self.fish if f.alive]
        biomass = sum(max(1.0, self._profile(f.scientific_name).max_length_cm or 5.0) for f in alive)
        target = max(22.0, self.equipment.heater_setpoint_c)
        self.water.temperature_c += (target - self.water.temperature_c) * min(1.0, .35 * dt)
        self.water.ammonia_mg_l += (biomass / max(self.volume_l, 1.0) * .0045 + self.feed_residue * .01) * dt
        self.feed_residue = max(0.0, self.feed_residue - .1 * dt)
        if self.equipment.filter_on:
            a = self.water.ammonia_mg_l * min(.55, .2 * self.equipment.filter_efficiency * dt)
            self.water.ammonia_mg_l -= a; self.water.nitrite_mg_l += a * .75
            n = self.water.nitrite_mg_l * min(.5, .18 * self.equipment.filter_efficiency * dt)
            self.water.nitrite_mg_l -= n; self.water.nitrate_mg_l += n * 2.8
        oxygen = (.28 if self.equipment.aeration_on else .04) + (.08 if self.equipment.light_on else -.04) * self.plant_biomass
        self.water.dissolved_oxygen_mg_l = min(9.5, max(1.5, self.water.dissolved_oxygen_mg_l + oxygen * dt - biomass / max(self.volume_l,1.0) * .12 * dt))
        self.water.nitrate_mg_l = max(0.0, self.water.nitrate_mg_l - (.15 * self.plant_biomass if self.equipment.light_on else .03) * dt)
        if self.mode == "marine":
            self.water.salinity_ppt += ((35.0 - self.water.salinity_ppt) * .12 if self.equipment.auto_topoff_on else .025) * dt
            if self.equipment.skimmer_on: self.water.ammonia_mg_l = max(0.0, self.water.ammonia_mg_l - .003 * dt)
        for fish in alive:
            self._tick_fish(fish, dt)

    def _tick_fish(self, fish: FishState, dt: float) -> None:
        p = self._profile(fish.scientific_name)
        same = sum(1 for f in self.fish if f.alive and f.scientific_name == fish.scientific_name)
        delta = -.75
        delta += self._range_penalty(self.water.temperature_c, p.temperature_min_c, p.temperature_max_c, 3.0)
        delta += self._range_penalty(self.water.ph, p.ph_min, p.ph_max, 8.0)
        if p.schooling_min > 1 and same < p.schooling_min: delta += min(12.0, 1.5 * (p.schooling_min - same))
        if p.territorial and same > max(1, p.schooling_min): delta += p.aggression * (3 + same - p.schooling_min)
        if p.anemone_association and self.mode == "marine": delta += .35
        if self.water.ammonia_mg_l > .02: delta += min(35.0, (self.water.ammonia_mg_l - .02) * 180)
        if self.water.nitrite_mg_l > .02: delta += min(30.0, (self.water.nitrite_mg_l - .02) * 150)
        if self.water.nitrate_mg_l > 40: delta += min(18.0, (self.water.nitrate_mg_l - 40) / 8)
        if self.water.dissolved_oxygen_mg_l < 5 and not p.air_breather: delta += (5 - self.water.dissolved_oxygen_mg_l) * 6
        if self.mode == "marine": delta += max(0.0, abs(self.water.salinity_ppt - 35) - 1.5) * 1.7
        fish.hunger = min(100.0, fish.hunger + 2.2 * dt)
        fish.stress = min(100.0, max(0.0, fish.stress + delta * dt))
        damage = max(0.0, (fish.stress - 65) * .045 + (fish.hunger - 90) * .08) * dt
        fish.health = min(100.0, max(0.0, fish.health - damage + (.12 * dt if fish.stress < 25 and fish.hunger < 60 else 0)))
        fish.energy = min(100.0, max(0.0, 100 - fish.hunger * .45 - fish.stress * .30)); fish.age_hours += dt
        if fish.health <= 0 and fish.alive:
            fish.alive = False; self._event("death", f"Погибла {fish.common_name} ({fish.scientific_name}).")

    def apply_action(self, action: dict[str, Any], actor: str = "leshy") -> str:
        kind = str(action.get("type") or "").lower()
        if kind == "feed":
            amount = max(.1, min(float(action.get("amount", 1)), 2.0))
            alive = [f for f in self.fish if f.alive]
            for f in alive: f.hunger = max(0.0, f.hunger - 45 * amount)
            self.feed_residue += max(0.0, amount - min(1.0, len(alive)/8)) * .8
            msg = f"Кормление: {amount:.1f} порции."
        elif kind == "water_change":
            pct = max(1.0, min(float(action.get("percent", 20)), 40.0)); k = 1 - pct/100
            self.water.ammonia_mg_l *= k; self.water.nitrite_mg_l *= k; self.water.nitrate_mg_l *= k
            self.water.ph += ((8.15 if self.mode == "marine" else 7.0) - self.water.ph) * (1-k)
            if self.mode == "marine": self.water.salinity_ppt += (35 - self.water.salinity_ppt) * (1-k)
            msg = f"Подмена воды: {pct:g}%."
        elif kind == "set_temperature":
            value = max(18.0, min(float(action.get("celsius",25)),31.0)); self.equipment.heater_setpoint_c = value; msg=f"Уставка {value:.1f} °C."
        elif kind in {"aeration","filter","light","skimmer","auto_topoff"}:
            if kind == "skimmer" and self.mode != "marine": raise ValueError("Скиммер только для морского режима")
            attr={"aeration":"aeration_on","filter":"filter_on","light":"light_on","skimmer":"skimmer_on","auto_topoff":"auto_topoff_on"}[kind]
            setattr(self.equipment,attr,bool(action.get("enabled",True))); msg=f"{kind}: {'включено' if getattr(self.equipment,attr) else 'выключено'}."
        elif kind == "clean_filter": self.equipment.filter_efficiency=min(1.0,self.equipment.filter_efficiency+.25); msg="Фильтр обслужен."
        elif kind in {"observe","do_nothing"}: msg="Наблюдение без вмешательства."
        else: raise ValueError(f"Неизвестное действие: {kind}")
        self._event("action",msg,actor); self._save(); return msg

    def leshy_step(self, llm: Any) -> dict[str, Any]:
        prompt={"task":"Считай это настоящим аквариумом-питомцем. Не допускай гибели и хронического стресса.","aquarium":self.snapshot(False),"allowed_actions":["feed","water_change","set_temperature","aeration","filter","light","skimmer","auto_topoff","clean_filter","observe"],"output":{"thought_summary":"коротко владельцу","actions":[{"type":"observe"}]}}
        raw=str(llm.chat([ChatMessage("system","Ты Леший. Заботься об аквариуме как о реальном. Ответ только JSON."),ChatMessage("user",json.dumps(prompt,ensure_ascii=False))]) or "")
        try:
            match=re.search(r"\{.*\}",raw,re.DOTALL); parsed=json.loads(match.group(0) if match else raw)
        except Exception:
            parsed={"thought_summary":"Не удалось разобрать решение; вмешательство отменено.","actions":[{"type":"observe"}]}
        actions=parsed.get("actions") if isinstance(parsed.get("actions"),list) else [{"type":"observe"}]
        safe=[]; applied=[]
        for action in [x for x in actions[:4] if isinstance(x,dict)]:
            try: applied.append(self.apply_action(action,"leshy")); safe.append(dict(action))
            except (ValueError,TypeError): pass
        self.last_leshy_message=str(parsed.get("thought_summary") or "")[:800]; self.last_leshy_actions=safe; self._save()
        return {"message":self.last_leshy_message,"actions":safe,"applied":applied,"state":self.snapshot()}

    def snapshot(self, include_events: bool = True) -> dict[str, Any]:
        profiles={}
        for f in self.fish:
            if f.scientific_name not in profiles: profiles[f.scientific_name]=asdict(self._profile(f.scientific_name))
        alive=[f for f in self.fish if f.alive]
        data={"mode":self.mode,"volume_l":self.volume_l,"sim_hour":round(self.sim_hour,3),"water":asdict(self.water),"equipment":asdict(self.equipment),"fish":[asdict(f) for f in self.fish],"species_profiles":profiles,"plants":{"health":self.plants_health,"biomass":self.plant_biomass},"last_leshy_message":self.last_leshy_message,"last_leshy_actions":self.last_leshy_actions,"summary":{"alive":len(alive),"dead":len(self.fish)-len(alive),"average_health":round(sum(f.health for f in alive)/max(1,len(alive)),2),"average_stress":round(sum(f.stress for f in alive)/max(1,len(alive)),2)}}
        if include_events: data["events"]=self.events[-100:]
        return data

    def _save(self) -> None:
        self.data_dir.mkdir(parents=True,exist_ok=True)
        data={"mode":self.mode,"volume_l":self.volume_l,"sim_hour":self.sim_hour,"water":asdict(self.water),"equipment":asdict(self.equipment),"fish":[asdict(f) for f in self.fish],"plants_health":self.plants_health,"plant_biomass":self.plant_biomass,"feed_residue":self.feed_residue,"events":self.events,"last_leshy_message":self.last_leshy_message,"last_leshy_actions":self.last_leshy_actions}
        self.state_path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")

    def _load(self) -> None:
        if not self.state_path.exists(): return
        try:
            p=json.loads(self.state_path.read_text(encoding="utf-8")); self.mode=str(p.get("mode") or "freshwater"); self.volume_l=float(p.get("volume_l") or 120); self.sim_hour=float(p.get("sim_hour") or 0)
            self.water=WaterState(**dict(p.get("water") or asdict(self._default_water(self.mode)))); self.equipment=EquipmentState(**dict(p.get("equipment") or {})); self.fish=[FishState(**x) for x in p.get("fish",[])]; self.plants_health=float(p.get("plants_health",90)); self.plant_biomass=float(p.get("plant_biomass",1)); self.feed_residue=float(p.get("feed_residue",0)); self.events=list(p.get("events",[]))[-250:]; self.last_leshy_message=str(p.get("last_leshy_message") or ""); self.last_leshy_actions=list(p.get("last_leshy_actions") or [])
        except Exception:
            self.mode,self.volume_l,self.sim_hour="freshwater",120.0,0.0; self.water=self._default_water("freshwater"); self.equipment=EquipmentState(); self.fish=[]; self.events=[]


__all__ = ["AquariumSimulation", "SpeciesProfile", "SpeciesProfileService"]
