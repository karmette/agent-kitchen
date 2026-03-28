"""Topology builder: translates a declarative topology config into OASIS
agent specs and group membership lists.

Three modes:
  pairwise — one group per (negotiator, counterparty) pair
  rooms    — one group per negotiator (or counterparty), with configurable members
  custom   — explicit group definitions by index
"""


def build_topology(config, negotiators, counterparties):
    """Build agent instances and group membership from a topology config.

    Args:
        config: topology dict from the profile JSON, e.g. {"mode": "pairwise"}
        negotiators: list of negotiator profile dicts
        counterparties: list of counterparty profile dicts

    Returns:
        agents: list of (agent_id, profile_dict, is_negotiator, source_index)
        groups: list of (group_name, [agent_ids])
    """
    mode = config.get("mode", "pairwise")

    if mode == "pairwise":
        return _build_pairwise(negotiators, counterparties)
    elif mode == "rooms":
        return _build_rooms(config, negotiators, counterparties)
    elif mode == "custom":
        return _build_custom(config, negotiators, counterparties)
    else:
        raise ValueError(f"Unknown topology mode: {mode}")


def _build_pairwise(negotiators, counterparties):
    """One group per (negotiator, counterparty) pair. Both agents duplicated."""
    agents = []
    groups = []
    n_cp = len(counterparties)
    agent_id = 0

    for i, neg in enumerate(negotiators):
        for j, cp in enumerate(counterparties):
            neg_id = agent_id
            cp_id = agent_id + 1
            agent_id += 2

            neg_profile = dict(neg, username=f"{neg['username']}_{j}")
            cp_profile = dict(cp, username=f"{cp['username']}_{i}")
            agents.append((neg_id, neg_profile, True, i))
            agents.append((cp_id, cp_profile, False, j))

            group_name = f"{neg['username']}_x_{cp['username']}"
            groups.append((group_name, [neg_id, cp_id]))

    return agents, groups


def _build_rooms(config, negotiators, counterparties):
    """One group per anchor agent, with configurable membership."""
    per = config.get("per", "negotiator")
    members = config.get("members", [])
    agents = []
    groups = []
    agent_id = 0

    if per == "negotiator":
        anchors = negotiators
        others = counterparties
        anchor_is_neg = True
    elif per == "counterparty":
        anchors = counterparties
        others = negotiators
        anchor_is_neg = False
    else:
        raise ValueError(f"Unknown 'per' value: {per}")

    for i, anchor in enumerate(anchors):
        room_agents = []

        for member_type in members:
            if member_type in ("negotiator", "counterparty"):
                # The single anchor agent for this room
                profile = dict(anchor, username=f"{anchor['username']}_{i}")
                is_neg = anchor_is_neg if member_type == per else not anchor_is_neg
                agents.append((agent_id, profile, is_neg, i))
                room_agents.append(agent_id)
                agent_id += 1

            elif member_type == "all_counterparties":
                for j, other in enumerate(others if anchor_is_neg else counterparties):
                    profile = dict(other, username=f"{other['username']}_{i}")
                    agents.append((agent_id, profile, not anchor_is_neg, j))
                    room_agents.append(agent_id)
                    agent_id += 1

            elif member_type == "all_negotiators":
                for j, other in enumerate(others if not anchor_is_neg else negotiators):
                    profile = dict(other, username=f"{other['username']}_{i}")
                    agents.append((agent_id, profile, anchor_is_neg or True, j))
                    room_agents.append(agent_id)
                    agent_id += 1

        group_name = f"room_{anchor['username']}_{i}"
        groups.append((group_name, room_agents))

    return agents, groups


def _build_custom(config, negotiators, counterparties):
    """Explicit group definitions by index."""
    agents = []
    groups = []
    agent_id = 0
    # Track shared agents: (role, source_index) -> agent_id
    shared_agents = {}

    for group_def in config["groups"]:
        group_name = group_def["name"]
        shared = group_def.get("shared", False)
        room_agents = []

        for idx in group_def.get("negotiators", []):
            neg = negotiators[idx]
            if shared:
                key = ("neg", idx)
                if key not in shared_agents:
                    agents.append((agent_id, dict(neg), True, idx))
                    shared_agents[key] = agent_id
                    agent_id += 1
                room_agents.append(shared_agents[key])
            else:
                agents.append((agent_id, dict(neg, username=f"{neg['username']}_{agent_id}"), True, idx))
                room_agents.append(agent_id)
                agent_id += 1

        for idx in group_def.get("counterparties", []):
            cp = counterparties[idx]
            if shared:
                key = ("cp", idx)
                if key not in shared_agents:
                    agents.append((agent_id, dict(cp), False, idx))
                    shared_agents[key] = agent_id
                    agent_id += 1
                room_agents.append(shared_agents[key])
            else:
                agents.append((agent_id, dict(cp, username=f"{cp['username']}_{agent_id}"), False, idx))
                room_agents.append(agent_id)
                agent_id += 1

        groups.append((group_name, room_agents))

    return agents, groups
