"""全项目枚举(对照 03-详细设计-数据模型 第 3 章)。"""
import enum


class Role(str, enum.Enum):
    admin = "admin"
    director = "director"
    writer = "writer"
    artist = "artist"
    producer = "producer"
    viewer = "viewer"


class AssetType(str, enum.Enum):
    character = "character"
    prop = "prop"
    location = "location"
    costume = "costume"
    vehicle = "vehicle"
    style = "style"
    model_lora = "model_lora"


class VersionStatus(str, enum.Enum):
    draft = "draft"
    review = "review"
    locked = "locked"
    archived = "archived"


class ProductionStatus(str, enum.Enum):
    to_design = "to_design"
    concept = "concept"
    generating = "generating"
    pending_review = "pending_review"
    revising = "revising"
    approved = "approved"
    in_cut = "in_cut"


class JobStatus(str, enum.Enum):
    pending = "pending"
    submitted = "submitted"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class RequestType(str, enum.Enum):
    audio = "audio"
    image = "image"
    video = "video"


class RefMode(str, enum.Enum):
    floating = "floating"
    pinned = "pinned"


class RelationType(str, enum.Enum):
    adaptation = "adaptation"
    composition = "composition"
    reference = "reference"
    reuse = "reuse"
    assembly = "assembly"


class ReviewStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class AnnotationKind(str, enum.Enum):
    image_box = "image_box"
    video_timecode = "video_timecode"
    comment = "comment"


class CutKind(str, enum.Enum):
    rough = "rough"
    fine = "fine"
    final = "final"


class ConsistencyStrategyKind(str, enum.Enum):
    prompt = "prompt"
    reference = "reference"
    lora = "lora"


class ProviderKind(str, enum.Enum):
    generation = "generation"
    llm = "llm"


class QuotaScope(str, enum.Enum):
    project = "project"
    user = "user"
