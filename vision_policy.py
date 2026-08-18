"""Política compartida para decidir cuándo una zona puede ejecutar visión."""


def zona_vision_activa(zona_cfg, perfiles, label):
    """Solo autoriza visión con zona habilitada, perfil válido y label asignado."""
    if not isinstance(zona_cfg, dict) or not zona_cfg.get("habilitado"):
        return False
    perfil_id = str(zona_cfg.get("perfil_id") or "").strip()
    if not perfil_id or perfil_id not in (perfiles or {}):
        return False
    return str(label or "") in zona_cfg.get("labels", ["person"])
