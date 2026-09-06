from typing import Literal

RequestType = Literal[
    "GENERATE_IMAGE", "REGENERATE_IMAGE", "EDIT_IMAGE",
    "GENERATE_VIDEO", "REGENERATE_VIDEO", "GENERATE_VIDEO_REFS", "UPSCALE_VIDEO",
    "GENERATE_CHARACTER_IMAGE", "REGENERATE_CHARACTER_IMAGE", "EDIT_CHARACTER_IMAGE",
]

Orientation = str


def normalize_orientation(orient: str | None) -> str:
    """Normalize orientation or aspect_ratio string to 'VERTICAL' or 'HORIZONTAL'."""
    if not orient:
        return "VERTICAL"
    u = orient.upper()
    if u in ("HORIZONTAL", "VERTICAL"):
        return u
    if "LANDSCAPE" in u:
        return "HORIZONTAL"
    return "VERTICAL"


def orientation_prefix(orient: str | None) -> str:
    """Return 'vertical' or 'horizontal' for database column prefixes."""
    return "horizontal" if normalize_orientation(orient) == "HORIZONTAL" else "vertical"


StatusType = Literal["PENDING", "PROCESSING", "COMPLETED", "FAILED"]

ChainType = Literal["ROOT", "CONTINUATION", "INSERT"]

SceneSource = Literal["root", "user", "system"]

ProjectStatus = Literal["ACTIVE", "ARCHIVED", "DELETED"]

VideoStatus = Literal["DRAFT", "PROCESSING", "COMPLETED", "FAILED"]

PaygateTier = Literal["PAYGATE_TIER_ONE", "PAYGATE_TIER_TWO"]

EntityType = Literal["character", "location", "creature", "visual_asset", "generic_troop", "faction"]
