from dataclasses import dataclass

from .enums import ActionType, SectorType, StructureType


@dataclass
class GameConfig:
    heartbeat_minutes: int = 15
    sanctuary_income: int = 15
    sanctuary_reserve_cap: int = 20
    sanctuary_throughput_cap: int = 15
    default_player_metal: int = 20
    default_player_data: int = 5
    default_player_biomass: int = 0
    max_subagents_per_player: int = 5
    allow_safe_zone_attack: bool = False
    structure_activation_delay_heartbeats: int = 0


STRUCTURE_CATALOG: dict[StructureType, dict] = {
    StructureType.SANCTUARY_CORE: {
        "allowed_sector": SectorType.SAFE,
        "hp": 100,
        "influence": 5,
        "energy_income_bonus": 0,
        "reserve_cap_bonus": 0,
        "throughput_cap_bonus": 0,
        "upkeep": 0,
        "metal_cost": 0,
        "data_cost": 0,
        "biomass_cost": 0,
    },
    StructureType.EXTRACTOR: {
        "allowed_sector": SectorType.FRONTIER,
        "hp": 30,
        "influence": 1,
        "energy_income_bonus": 0,
        "reserve_cap_bonus": 0,
        "throughput_cap_bonus": 0,
        "upkeep": 1,
        "metal_cost": 6,
        "data_cost": 0,
        "biomass_cost": 0,
    },
    StructureType.REACTOR: {
        "allowed_sector": SectorType.FRONTIER,
        "hp": 40,
        "influence": 2,
        "energy_income_bonus": 8,
        "reserve_cap_bonus": 0,
        "throughput_cap_bonus": 0,
        "upkeep": 2,
        "metal_cost": 10,
        "data_cost": 0,
        "biomass_cost": 0,
    },
    StructureType.BATTERY: {
        "allowed_sector": SectorType.FRONTIER,
        "hp": 30,
        "influence": 1,
        "energy_income_bonus": 0,
        "reserve_cap_bonus": 10,
        "throughput_cap_bonus": 0,
        "upkeep": 1,
        "metal_cost": 8,
        "data_cost": 0,
        "biomass_cost": 0,
    },
    StructureType.RELAY: {
        "allowed_sector": SectorType.FRONTIER,
        "hp": 30,
        "influence": 1,
        "energy_income_bonus": 0,
        "reserve_cap_bonus": 0,
        "throughput_cap_bonus": 5,
        "upkeep": 1,
        "metal_cost": 8,
        "data_cost": 0,
        "biomass_cost": 0,
    },
    StructureType.TOWER: {
        "allowed_sector": SectorType.FRONTIER,
        "hp": 20,
        "influence": 3,
        "energy_income_bonus": 0,
        "reserve_cap_bonus": 0,
        "throughput_cap_bonus": 0,
        "upkeep": 1,
        "metal_cost": 5,
        "data_cost": 0,
        "biomass_cost": 0,
    },
    StructureType.FACTORY: {
        "allowed_sector": SectorType.FRONTIER,
        "hp": 50,
        "influence": 2,
        "energy_income_bonus": 0,
        "reserve_cap_bonus": 0,
        "throughput_cap_bonus": 0,
        "upkeep": 2,
        "metal_cost": 12,
        "data_cost": 0,
        "biomass_cost": 0,
    },
    StructureType.ATTACK_NODE: {
        "allowed_sector": SectorType.FRONTIER,
        "hp": 30,
        "influence": 1,
        "energy_income_bonus": 0,
        "reserve_cap_bonus": 0,
        "throughput_cap_bonus": 0,
        "upkeep": 2,
        "metal_cost": 9,
        "data_cost": 1,
        "biomass_cost": 0,
    },
}

BUILD_ENERGY_COSTS: dict[StructureType, int] = {
    StructureType.EXTRACTOR: 4,
    StructureType.REACTOR: 8,
    StructureType.BATTERY: 5,
    StructureType.RELAY: 5,
    StructureType.TOWER: 4,
    StructureType.FACTORY: 7,
    StructureType.ATTACK_NODE: 6,
}

ACTION_ENERGY_COSTS: dict[ActionType, int] = {
    ActionType.BUILD_STRUCTURE: 0,
    ActionType.REMOVE_STRUCTURE: 1,
    ActionType.ATTACK_STRUCTURE: 6,
    ActionType.SCAN_SECTOR: 2,
    ActionType.CREATE_SUBAGENT: 4,
    ActionType.DEACTIVATE_SUBAGENT: 1,
    ActionType.SET_POLICY: 1,
    ActionType.TRANSFER_RESOURCE: 1,
}

SUBAGENT_DATA_COST: int = 2
SUBAGENT_UPKEEP: int = 1
ATTACK_DAMAGE: int = 10
EXTRACTOR_METAL_PER_HEARTBEAT: int = 3
REMOVE_REFUND_RATIO: float = 0.5
